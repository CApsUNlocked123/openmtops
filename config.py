import os
from dotenv import load_dotenv

load_dotenv()  # reads .env from project root (no-op if file absent)

# Read credentials via runtime_config so config.json takes priority over .env.
# runtime_config itself falls back to os.environ for power users who use .env only.
from runtime_config import get as _cfg, get_telegram_credentials, get_dhan_credentials

_tg_id, _tg_hash = get_telegram_credentials()
API_APP  = _tg_id  or os.getenv("TELEGRAM_API_APP",  "")
API_HASH = _tg_hash or os.getenv("TELEGRAM_API_HASH", "")
APP      = "OpenMTOps"

_d_id, _d_tok = get_dhan_credentials()
DHAN_CLIENTID    = _d_id  or os.getenv("DHAN_CLIENTID",    "")
DHAN_ACCESSTOKEN = _d_tok  or os.getenv("DHAN_ACCESSTOKEN", "")
DHAN_APIKEY      = _cfg("dhan.api_key")    or os.getenv("DHAN_APIKEY",   "")
DHAN_APISECRET   = _cfg("dhan.api_secret") or os.getenv("DHAN_APISECRET","")

# ── Strategy Dashboard state ───────────────────────────────────────────────────
PCR_SERIES: list = []   # rolling PCR readings for trend health (capped at 100)
PHASE_LOG: list  = []   # [{phase, start_time, end_time}] — reset each trading day
REGIME_HISTORY: dict = {}   # {instrument: [regime, ...]} — for whipsaw detection
