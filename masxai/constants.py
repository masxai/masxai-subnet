"""
masxai/constants.py — v1 subnet constants for netuid 501 (testnet).
Keep this list short. Do not add v3 constants here.
"""

NETUID = 501
NETWORK = "test"
SUBTENSOR_ENDPOINT = "wss://test.finney.opentensor.ai:443"

# --- forecast target (v1: short-horizon TAO price direction) ---
FORECAST_ASSET = "bittensor"          # CoinGecko coin id for TAO
FORECAST_HORIZON_SECONDS = 3600       # 60-minute resolution horizon

# --- query / scoring ---
QUERY_TIMEOUT = 12                    # dendrite query timeout (seconds)
EMA_ALPHA = 0.1                       # score smoothing; higher = faster adaptation
NEUTRAL_PROB = 0.5                    # baseline / fallback probability
NO_ANSWER_BRIER = 0.5                 # Brier assigned when a miner doesn't answer
PROB_CLAMP_LO = 0.01
PROB_CLAMP_HI = 0.99

# --- weights ---
MIN_RESOLVED_BEFORE_WEIGHTS = 1       # set weights once anything has resolved

# --- persistence ---
STATE_FILE = "validator_state.json"   # pending + resolved + scores

# --- oracle ---
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
ORACLE_TIMEOUT = 10                   # seconds
