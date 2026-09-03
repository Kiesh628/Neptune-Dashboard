import os
from dotenv import load_dotenv

load_dotenv()

RUNTIME_DIR = "runtime"
TRIALS_DIR = "runtime/trials"
EXTENDED_DIR = "runtime/extended"
TELEMETRY_DIR = "runtime/telemetry"

for path in [RUNTIME_DIR, TRIALS_DIR, EXTENDED_DIR, TELEMETRY_DIR]:
    os.makedirs(path, exist_ok=True)


DEFAULT_PARAMETERS = {'P_mean': 2.5e6,            # mean pressure [Pa]
                      'freq': 60.0,              # operating frequency [Hz]
                      'clearance_ratio_c': 0.1,  # dimensionless clearance ratio
                      'clearance_ratio_e': 0.1,  # dimensionless clearance ratio
                      'Porosity': 0.68,          # dimensionless regenerator porosity
                      'N_cells': 50}             # spatial grid cells

SAMPLE_RANGES = {'Length': [0.035, 0.065],     # regenerator length [m]
                 'Area':   [1.9e-5, 8.0e-5],       # cross-sectional area [m²]
                 'Dh':     [2.0e-5, 8.0e-5],         # matrix hydraulic diameter [m]
                 'Phase':  [-90.0, -10.0],        # phase shift [degrees]
                 'V_exp':  [1.0e-7, 1.0e-6],      # expander swept volume [m³]
                 'V_comp': [1.0e-6, 6.0e-6]}     # compressor swept volume [m³]

HEAT_EXCHANGER_EFFICENCIES = 0.95

STUDY_NAME = "Cryocooler_Optimization_14"
CHX_HEAT_LOAD = 1.0                              # static heat load at CHX [W]
CHX_THERMAL_MASS = 0.5                           # thermal mass at CHX [J/K]

MIN_DT_DT = -1.0                                 # stop if dT/dt > MIN_DT_DT [K/s]
DT_DT_CHECK_DELAY = 5.0                          # seconds before applying dT/dt threshold

CORES_RESERVED = 0

REGENERATOR_MATERIAL = 'SS304L'

GOOGLE_DATABASE_PASSWORD = os.getenv("GOOGLE_DATABASE_PASSWORD")
GOOGLE_DATABASE_IP = os.getenv("GOOGLE_DATABASE_IP")

CLOUD_DB_URL = f"postgresql://postgres:{GOOGLE_DATABASE_PASSWORD}@{GOOGLE_DATABASE_IP}:5432/postgres"

GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

RUN_DASHBOARD = False