import os
import sqlalchemy
from sqlalchemy.pool import QueuePool
from control import CLOUD_DB_URL

# Singleton DB Engine
_db_engine = None

def ensure_app_tables(engine):
    """Ensure custom application tables (live_telemetry, command_queue) exist in the database."""
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("""
                CREATE TABLE IF NOT EXISTS live_telemetry (
                    id SERIAL PRIMARY KEY,
                    trial_id INT NOT NULL,
                    simulated_time FLOAT NOT NULL,
                    chx_temp FLOAT NOT NULL,
                    p_reg TEXT,
                    t_gas TEXT,
                    t_solid TEXT,
                    u_reg TEXT,
                    comp_p FLOAT,
                    comp_t FLOAT,
                    exp_p FLOAT,
                    exp_t FLOAT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS command_queue (
                    id SERIAL PRIMARY KEY,
                    trial_id INT NOT NULL,
                    command TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            conn.commit()
    except Exception as e:
        print(f"[Database Warning] Application table verification failed: {e}")

def get_db_engine():
    """Retrieve or initialize the SQLAlchemy database engine singleton with QueuePool."""
    global _db_engine
    if _db_engine is None:
        try:
            _db_engine = sqlalchemy.create_engine(
                CLOUD_DB_URL,
                poolclass=QueuePool,
                pool_size=2,              # Minimal connections per process
                max_overflow=3,           # Slight buffer for spikes
                pool_timeout=30,
                pool_recycle=1800,        # Keep connections fresh for GCP
                connect_args={"connect_timeout": 10}
            )
            ensure_app_tables(_db_engine)
        except Exception as e:
            print(f"[Database Error] Failed to create database engine: {e}")
            _db_engine = None
    return _db_engine

def ensure_study_exists(study_name: str, storage_url: str, sampler=None):
    """Load an existing study or create it automatically if it does not exist in RDB storage."""
    import optuna
    from sqlalchemy.pool import QueuePool
    
    # Optuna needs the exact same QueuePool settings to prevent connection spam
    storage = optuna.storages.RDBStorage(
        url=storage_url,
        engine_kwargs={
            "poolclass": QueuePool,
            "pool_size": 2,
            "max_overflow": 3,
            "pool_recycle": 1800,
            "connect_args": {"connect_timeout": 10}
        }
    )
    try:
        return optuna.load_study(study_name=study_name, storage=storage, sampler=sampler)
    except KeyError:
        return optuna.create_study(
            study_name=study_name,
            storage=storage,
            direction="minimize",
            load_if_exists=True,
            sampler=sampler
        )