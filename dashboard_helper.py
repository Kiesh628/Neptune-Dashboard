import re
import json
import optuna
import pandas as pd
import sqlalchemy
from sqlalchemy import text
import math
import io
import os
import streamlit as st

from googleapiclient.http import MediaIoBaseDownload
from control import STUDY_NAME, RUNTIME_DIR
from infra.database import get_db_engine
from infra.gdrive import get_drive_service, get_completed_trials_file_map, get_all_filenames_in_folder

db_engine = get_db_engine()
assert db_engine is not None

METADATA_CACHE_FILE = os.path.join(RUNTIME_DIR, f"trial_metadata_cache_{STUDY_NAME}.csv")

def get_all_completed_data_cached() -> list[dict]:
    if os.path.exists(METADATA_CACHE_FILE):
        df_cache = pd.read_csv(METADATA_CACHE_FILE)
    else:
        df_cache = pd.DataFrame(columns=['trial_id', 'stall_temp', 'time_near_stall'])

    query = text("""
        SELECT t.number
        FROM trials t
        JOIN studies s ON t.study_id = s.study_id
        WHERE s.study_name = :study_name AND t.state = 'COMPLETE'
    """)
    if db_engine is None: return []
    with db_engine.connect() as conn:
        df_comp = pd.read_sql(query, conn, params={"study_name": STUDY_NAME})
    completed_ids = df_comp['number'].tolist()
    
    cached_ids = df_cache['trial_id'].tolist()
    missing_ids = [tid for tid in completed_ids if tid not in cached_ids]

    if missing_ids:
        missing_ids_str = ','.join(map(str, missing_ids))
        q_missing = text(f"""
            SELECT t.number, v.value,
                   MAX(CASE WHEN ua.key = 'fit_a' THEN ua.value_json END) as fit_a,
                   MAX(CASE WHEN ua.key = 'fit_b' THEN ua.value_json END) as fit_b
            FROM trials t
            JOIN studies s ON t.study_id = s.study_id
            LEFT JOIN trial_values v ON t.trial_id = v.trial_id
            LEFT JOIN trial_user_attributes ua ON t.trial_id = ua.trial_id
            WHERE s.study_name = :study_name AND t.number IN ({missing_ids_str})
            GROUP BY t.number, v.value
        """)
        with db_engine.connect() as conn:
            df_missing = pd.read_sql(q_missing, conn, params={"study_name": STUDY_NAME})
        
        new_records = []
        import json
        for _, row in df_missing.iterrows():
            tid = int(row['number'])
            c_val = float(row['value']) if pd.notnull(row['value']) else None
            a_val = json.loads(row['fit_a']) if pd.notnull(row['fit_a']) else None
            b_val = json.loads(row['fit_b']) if pd.notnull(row['fit_b']) else None
            
            time_to_stall = None
            if a_val is not None and b_val is not None and c_val is not None:
                try:
                    ratio = c_val / (100.0 * a_val)
                    if ratio > 0 and b_val != 0:
                        time_to_stall = (1.0 / b_val) * math.log(ratio)
                except Exception as e:
                    print(f"Math error calculating stall time for trial {tid}: {e}")
            
            new_records.append({
                'trial_id': tid,
                'stall_temp': c_val,
                'time_near_stall': time_to_stall
            })
        
        df_new = pd.DataFrame(new_records)
        if df_cache.empty:
            df_cache = df_new
        else:
            df_cache = df_cache.astype(df_new.dtypes)
            df_cache = pd.concat([df_cache, df_new], ignore_index=True)
        df_cache.to_csv(METADATA_CACHE_FILE, index=False)

    final_df = df_cache[df_cache['trial_id'].isin(completed_ids)]
    
    return final_df.to_dict(orient='records')

@st.cache_data(ttl=600)
def get_completed_trial_csv(trial_id: int) -> pd.DataFrame | None:
    file_map = get_ids_from_drive()
    if trial_id not in file_map:
        return None
        
    try:
        from infra.gdrive import get_drive_service
        import requests
        
        service = get_drive_service()
        creds = service._http.credentials
        
        # Ensure token is fresh
        if not creds.valid:
            import google.auth.transport.requests
            creds.refresh(google.auth.transport.requests.Request())
            
        # Download directly via HTTP to maximize speed and bypass chunking overhead
        url = f"https://www.googleapis.com/drive/v3/files/{file_map[trial_id]}?alt=media"
        headers = {"Authorization": f"Bearer {creds.token}"}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        
        return pd.read_parquet(io.BytesIO(resp.content))
    except Exception as e:
        print(f"Error downloading Parquet for Trial {trial_id}: {e}")
        return None

@st.cache_data(ttl=2)
def get_live_telemetry_history(trial_id: int, time_window_sec: float | None = None):
    query = text("""
        SELECT simulated_time, chx_temp
        FROM live_telemetry 
        WHERE trial_id = :tid 
        ORDER BY simulated_time ASC
    """)
    if db_engine is None:
        return pd.DataFrame()
    with db_engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"tid": trial_id})
        
    if not df.empty and time_window_sec is not None:
        max_time = df['simulated_time'].max()
        df = df[df['simulated_time'] >= max(0, max_time - time_window_sec)]
        
    return df


@st.cache_data(ttl=60)
def get_ids_from_drive() -> dict:
    from infra.gdrive import get_completed_trials_file_map
    return get_completed_trials_file_map()

def get_study_metrics() -> dict:
    query = text("""
        SELECT t.number, t.state, v.value
        FROM trials t
        JOIN studies s ON t.study_id = s.study_id
        LEFT JOIN trial_values v ON t.trial_id = v.trial_id
        WHERE s.study_name = :study_name
    """)
    if db_engine is None:
        return {"best_temp": None, "second_best_temp": None, "best_trial_id": None, "pending_count": 0, "total_count": 0, "pending_trials": [], "completed_trials": []}
    
    with db_engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"study_name": STUDY_NAME})
        
    completed_df = df[(df['state'] == 'COMPLETE') & (df['value'].notnull())].sort_values('value')
    pending_df = df[df['state'] == 'RUNNING']
    
    best_temp = None
    second_best_temp = None
    best_id = None
    
    if len(completed_df) > 0:
        best_temp = float(completed_df.iloc[0]['value'])
        best_id = int(completed_df.iloc[0]['number'])
    if len(completed_df) > 1:
        second_best_temp = float(completed_df.iloc[1]['value'])

    return {
        "best_temp": best_temp,
        "second_best_temp": second_best_temp,
        "best_trial_id": best_id,
        "pending_count": len(pending_df),
        "total_count": len(df),
        "pending_trials": [int(x) for x in pending_df['number'].tolist()],
        "completed_trials": [int(x) for x in completed_df['number'].tolist()]
    }

def get_completed_trial_data(trial_id: int) -> dict:
    q_single = text("""
        SELECT t.number, v.value,
               MAX(CASE WHEN ua.key = 'fit_a' THEN ua.value_json END) as fit_a,
               MAX(CASE WHEN ua.key = 'fit_b' THEN ua.value_json END) as fit_b
        FROM trials t
        JOIN studies s ON t.study_id = s.study_id
        LEFT JOIN trial_values v ON t.trial_id = v.trial_id
        LEFT JOIN trial_user_attributes ua ON t.trial_id = ua.trial_id
        WHERE s.study_name = :study_name AND t.number = :tid
        GROUP BY t.number, v.value
    """)
    if db_engine is None:
        return {"trial_id": trial_id, "error": "Database not connected"}
    
    with db_engine.connect() as conn:
        df_single = pd.read_sql(q_single, conn, params={"study_name": STUDY_NAME, "tid": trial_id})
        
    if df_single.empty:
        return {"trial_id": trial_id, "error": "Trial ID not found."}
        
    row = df_single.iloc[0]
    import json
    c_val = float(row['value']) if pd.notnull(row['value']) else None
    a_val = json.loads(row['fit_a']) if pd.notnull(row['fit_a']) else None
    b_val = json.loads(row['fit_b']) if pd.notnull(row['fit_b']) else None
    
    time_to_stall = None
    if a_val is not None and b_val is not None and c_val is not None:
        try:
            ratio = c_val / (100.0 * a_val)
            if ratio > 0 and b_val != 0:
                time_to_stall = (1.0 / b_val) * math.log(ratio)
        except Exception as e:
            print(f"Math error calculating stall time for trial {trial_id}: {e}")
            
    return {
        "trial_id": trial_id,
        "stall_temp": c_val,
        "curve_fit_a": a_val,
        "curve_fit_b": b_val,
        "time_near_stall": time_to_stall
    }


def get_pending_trial_data(trial_id: int) -> dict:
    query = f"""
        SELECT simulated_time, chx_temp 
        FROM live_telemetry 
        WHERE trial_id = {trial_id} 
        ORDER BY simulated_time DESC 
        LIMIT 1;
    """
    if db_engine is None:
        return {"trial_id": trial_id, "error": "Database not connected"}
    try:
        with db_engine.connect() as conn:
            result = pd.read_sql(query, conn)
            
        if result.empty:
            return {"trial_id": trial_id, "time": None, "chx_temp": None}
            
        return {
            "trial_id": trial_id,
            "time": result['simulated_time'].iloc[0],
            "chx_temp": result['chx_temp'].iloc[0]
        }
    except Exception as e:
        return {"trial_id": trial_id, "error": str(e)}

def clear_live_telemetry(trial_id: int) -> bool:
    query = text("DELETE FROM live_telemetry WHERE trial_id = :tid")
    if db_engine is None:
        return False
    try:
        with db_engine.connect() as conn:
            conn.execute(query, {"tid": trial_id})
            conn.commit() 
        return True
    except Exception as e:
        print(f"Error clearing telemetry for trial {trial_id}: {e}")
        return False