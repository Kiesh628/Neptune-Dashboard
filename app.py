import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import json
import optuna
from sqlalchemy import text
import sys
import os
import math

import dashboard_helper as dh
from control import CLOUD_DB_URL, STUDY_NAME, EXTENDED_DIR, TELEMETRY_DIR, TRIALS_DIR
from infra.database import ensure_study_exists

st.set_page_config(page_title='Neptune Sim Dashboard', layout='wide')

if 'local_workers' not in st.session_state:
    st.session_state.local_workers = {}
    

def get_trial_max_time(trial_id: int) -> float:
    """Determine the maximum simulated time a trial has currently run for."""
    ext_file = os.path.join(EXTENDED_DIR, f"extended_{trial_id}_data.parquet")
    trial_file = os.path.join(TRIALS_DIR, f"trial_{trial_id}_data.parquet")
    
    for fpath in [ext_file, trial_file]:
        if os.path.exists(fpath):
            try:
                df = pd.read_parquet(fpath, columns=["Time"])
                if not df.empty:
                    return float(df["Time"].iloc[-1])
            except Exception:
                pass
    return 12.0

def get_optuna_study():
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    try:
        return ensure_study_exists(STUDY_NAME, CLOUD_DB_URL)
    except Exception as e:
        st.sidebar.error(f"Optuna connection error: {e}")
        return None

def send_stop_command(trial_id):
    query = text("INSERT INTO command_queue (trial_id, command) VALUES (:tid, 'STOP') ON CONFLICT (trial_id) DO NOTHING")
    engine = dh.db_engine
    if engine is None:
        st.toast("Error: Database not connected", icon="❌")
        return
    with engine.connect() as conn:
        conn.execute(query, {"tid": trial_id})
        conn.commit()
    st.toast(f"STOP command sent to Trial {trial_id}!", icon="🛑")

def handle_toasts(metrics):
    curr_pending = set(metrics['pending_trials'])
    curr_completed = set(metrics['completed_trials'])
    if 'prev_pending' in st.session_state:
        new_starts = curr_pending - st.session_state.prev_pending
        new_completes = curr_completed - st.session_state.prev_completed
        for tid in new_starts: st.toast(f"Trial {tid} started!", icon="🚀")
        for tid in new_completes: st.toast(f"Trial {tid} completed!", icon="✅")
    st.session_state.prev_pending = curr_pending
    st.session_state.prev_completed = curr_completed

@st.cache_data(ttl=30)
def get_drive_filenames_cached():
    return dh.get_all_filenames_in_folder()

def render_ongoing_detail(trial_id):
    st.button("⬅️ Back to Fleet Overview", on_click=lambda: st.query_params.clear())
    st.divider()
    
    c1, c2 = st.columns([3, 1])
    c1.header(f"Live Telemetry: Trial {trial_id}")
    
    if c2.button("🛑 STOP & FIT TRIAL", type="primary", width='stretch'):
        send_stop_command(trial_id)
        
    df_live = dh.get_live_telemetry_history(trial_id)
    if df_live.empty:
        st.info("Waiting for first telemetry packet...")
        return
        
    latest_row = df_live.iloc[-1]
    
    st.markdown(f"### Current CHX Temp: **{latest_row['chx_temp']:.2f} K**")
    st.markdown(f"### Time Elapsed: **{latest_row['simulated_time']:.2f} s**")
    
    st.info("Simulation in progress...")
    
    st.info("Plots have been disabled to conserve VM memory and Database bandwidth.")

# ==========================================
# COMPLETED DETAIL VIEW
# ==========================================
def render_completed_detail(trial_id):
    st.button("⬅️ Back to Fleet Overview", on_click=lambda: st.query_params.clear())
    st.divider()
    
    data = dh.get_completed_trial_data(trial_id)
    st.header(f"Completed Trial {trial_id} Dashboard")
    st.write(f"**Final Stall Temp:** `{data.get('stall_temp'):.2f} K`" if data.get('stall_temp') else "**Trial failed to stall.**")
    
    with st.spinner("Downloading CSV from Google Drive..."):
        df = dh.get_completed_trial_csv(trial_id)
        
    if df is None or df.empty:
        st.error("Could not load CSV from Google Drive.")
        return
        
    st.plotly_chart(px.line(df, x='Time', y='T_Wall_CHX', title="CHX Wall Cooldown"), width='stretch')

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(px.line(df, x='Time', y=['Comp_P', 'Exp_P'], title="Chamber Pressures (Pa)"), width='stretch')
    with c2:
        st.plotly_chart(px.line(df, x='Time', y=['Comp_T', 'Exp_T'], title="Chamber Temperatures (K)"), width='stretch')

    st.markdown("### 3D Regenerator Fields")
    tab1, tab2, tab3, tab4 = st.tabs(["Gas Temperature", "Solid Temperature", "Pressure Field", "Velocity Field"])
    
    # Downsample for 3D surfaces to prevent freezing the frontend via massive JSON serialization
    df_3d = df.iloc[::max(1, len(df) // 300)]
    
    def create_surface(cols, z_title):
        if not cols: return None
        fig = go.Figure(data=[go.Surface(z=df_3d[cols].values, x=np.linspace(0, 1, len(cols)), y=df_3d['Time'].values, colorscale='Plasma')])
        fig.update_layout(scene=dict(xaxis_title='Position', yaxis_title='Time (s)', zaxis_title=z_title), height=600)
        return fig

    with tab1:
        tg_cols = [c for c in df.columns if c.startswith('TG_')]
        if fig := create_surface(tg_cols, 'Temperature (K)'):
            st.plotly_chart(fig, width='stretch')
            
    with tab2:
        ts_cols = [c for c in df.columns if c.startswith('TS_')]
        if fig := create_surface(ts_cols, 'Temperature (K)'):
            st.plotly_chart(fig, width='stretch')
            
    with tab3:
        p_cols = [c for c in df.columns if c.startswith('P_')]
        if fig := create_surface(p_cols, 'Pressure (Pa)'):
            st.plotly_chart(fig, width='stretch')
            
    with tab4:
        u_cols = [c for c in df.columns if c.startswith('U_')]
        if fig := create_surface(u_cols, 'Velocity (m/s)'):
            st.plotly_chart(fig, width='stretch')

# ==========================================
# MASTER VIEW
# ==========================================
def render_master_view(metrics):
    st.title("Simulation Dashboard")
    if st.button("🔄 Refresh Data (Manual)"): st.rerun()
    st.divider()
    
    # Top Metrics
    c1, c2, c3, c4 = st.columns(4)
    best_temp_str = f"{metrics['best_temp']:.2f} K" if metrics['best_temp'] else "N/A"
    diff = (metrics['best_temp'] - metrics['second_best_temp']) if metrics['second_best_temp'] else 0.0
    
    c1.metric('Best Stall Temperature', best_temp_str, f"{diff:.2f} K" if diff else None, "inverse")
    c2.metric('Best Trial ID', str(metrics["best_trial_id"]) if metrics["best_trial_id"] is not None else "N/A")
    c3.metric('Ongoing Trials', str(metrics["pending_count"]))
    c4.metric('Total Trials', str(metrics["total_count"]))
    
    st.divider()
    
    tab_ongoing, tab_completed = st.tabs(["🔴 Cloud Ongoing", "✅ Completed Trials"])
    
    # --- ONGOING TRIALS TAB ---
    with tab_ongoing:
        sort_ongoing = st.selectbox("Sort Ongoing By:", ["Current Temp", "Time Elapsed", "Trial ID"], key="sort_o")
        if not metrics["pending_trials"]:
            st.info("No active workers.")
        else:
            live_data = [dh.get_pending_trial_data(tid) for tid in metrics["pending_trials"]]
            if sort_ongoing == "Current Temp": live_data.sort(key=lambda x: x.get('chx_temp', 999) or 999)
            elif sort_ongoing == "Time Elapsed": live_data.sort(key=lambda x: x.get('time', -1) or -1, reverse=True)
            elif sort_ongoing == "Trial ID": live_data.sort(key=lambda x: x['trial_id'], reverse=True)
            
            for dat in live_data:
                tid, t_sim, chx_t = dat['trial_id'], dat.get('time'), dat.get('chx_temp')
                with st.container(border=True):
                    cols = st.columns([1, 2, 2, 4, 2])
                    cols[0].markdown(f"**Trial {tid}**")
                    if t_sim is not None and chx_t is not None:
                        cols[1].markdown(f"⏱️ {t_sim:.2f} s")
                        cols[2].markdown(f"🌡️ **{chx_t:.1f} K**")
                        
                        cols[3].markdown("🔄 Simulating...")
                    else:
                        cols[1].markdown("Initializing..."); cols[2].markdown("-"); cols[3].markdown("-")
                        
                    if cols[4].button(f"🔍 View {tid}", key=f"btn_{tid}"):
                        st.query_params["trial_id"] = str(tid)
                        st.query_params["mode"] = "ongoing"
                        st.rerun()

    # --- COMPLETED TRIALS TAB ---
    with tab_completed:
        sort_completed = st.selectbox("Sort Completed By:", ["Stall Temp", "Trial ID"], key="sort_c")
        
        if not metrics["completed_trials"]:
            st.info("No completed trials.")
        else:
            # NEW: Fetch everything via the cache helper
            with st.spinner("Loading cached trial metadata..."):
                comp_data = dh.get_all_completed_data_cached()
            
            # Fetch list of Drive filenames to check availability (cached for 30s)
            with st.spinner("Checking file availability on Google Drive..."):
                drive_files = set(get_drive_filenames_cached())

            # Sorting logic for the list of dictionaries
            if sort_completed == "Stall Temp": 
                comp_data.sort(key=lambda x: x.get('stall_temp') or 999)
            elif sort_completed == "Trial ID": 
                comp_data.sort(key=lambda x: x['trial_id'], reverse=True)
            
            # Slice to top 30 to prevent massive Streamlit UI lag
            comp_data = comp_data[:30]
            st.caption(f"Showing top {len(comp_data)} completed trials")
            
            for dat in comp_data:
                tid = int(dat['trial_id'])
                stall_t = dat.get('stall_temp')
                t_stall = dat.get('time_near_stall')
                is_available = f"trial_{tid}_data.parquet" in drive_files or f"extended_{tid}_data.parquet" in drive_files
                
                with st.container(border=True):
                    cols = st.columns([1, 1.8, 1.8, 1.8, 1.8])
                    cols[0].markdown(f"**Trial {tid}**")
                    
                    # Display values (Handle None cases)
                    st_val = f"{stall_t:.2f} K" if stall_t is not None else "Failed"
                    cols[1].markdown(f"Stall: **{st_val}**")
                    
                    cols[2].markdown(f"")
                    
                    # Availability marker
                    if is_available:
                        cols[3].markdown("🟢 **Available**")
                    else:
                        cols[3].markdown("🔴 **Missing**")
                    
                    if cols[4].button(f"📊 Analyze", key=f"btn_c_{tid}"):
                        st.query_params["trial_id"] = str(tid)
                        st.query_params["mode"] = "completed"
                        st.rerun()
                        
                    # EXTEND BUTTON LOGIC

def main():
    metrics = dh.get_study_metrics()
    handle_toasts(metrics)

    # Check if we are in a sub-view
    if "trial_id" in st.query_params:
        target_id = int(st.query_params["trial_id"])
        mode = st.query_params.get("mode", "cloud") # Default to cloud if mode missing

        if target_id in metrics["pending_trials"]: 
            render_ongoing_detail(target_id)
        elif target_id in metrics["completed_trials"]: 
            render_completed_detail(target_id)
        else: 
            st.warning("Trial not found.")
            st.button("⬅️ Back", on_click=lambda: st.query_params.clear())
    else:
        render_master_view(metrics)

if __name__ == "__main__":
    if st.runtime.exists(): main() #type:ignore
    else: os.system(f"{sys.executable} -m streamlit run {os.path.abspath(__file__)}")