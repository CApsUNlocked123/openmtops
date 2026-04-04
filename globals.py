import os
from dotenv import load_dotenv

load_dotenv()  # reads .env from project root (no-op if file absent)

API_APP  = os.getenv("TELEGRAM_API_APP",  "")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
APP      = "CATrader"

DHAN_ACCESSTOKEN = os.getenv("DHAN_ACCESSTOKEN", "")
DHAN_CLIENTID    = os.getenv("DHAN_CLIENTID",    "")
DHAN_APIKEY      = os.getenv("DHAN_APIKEY",      "")
DHAN_APISECRET   = os.getenv("DHAN_APISECRET",   "")

# ── Strategy Dashboard state ───────────────────────────────────────────────────
PCR_SERIES: list = []   # rolling PCR readings for trend health (capped at 100)
PHASE_LOG: list  = []   # [{phase, start_time, end_time}] — reset each trading day
