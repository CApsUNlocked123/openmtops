import json
import os
import pandas as pd
from dhanhq import DhanContext, dhanhq
from runtime_config import get_dhan_credentials

DHAN_CLIENTID, DHAN_ACCESSTOKEN = get_dhan_credentials()

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dhan_token.json")

# def load_token() -> str:
#     if os.path.exists(TOKEN_FILE):
#         with open(TOKEN_FILE) as f:
#             return json.load(f).get("access_token", DHAN_ACCESSTOKEN)
#     return DHAN_ACCESSTOKEN

# def save_token(access_token: str) -> None:
#     with open(TOKEN_FILE, "w") as f:
#         json.dump({"access_token": access_token}, f)

dhan_context = DhanContext(DHAN_CLIENTID, DHAN_ACCESSTOKEN)
dhan = dhanhq(dhan_context)
pos = dhan.get_positions()
print(pos)


# ── Instrument master ─────────────────────────────────────────────────────────
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
instruments = pd.read_csv(SCRIP_MASTER_URL, low_memory=False)
instruments["_expiry_dt"] = pd.to_datetime(instruments["SEM_EXPIRY_DATE"], errors="coerce")

def lookup_security(symbol: str, strike, option_type: str) -> dict | None:
    """
    Find the nearest upcoming expiry security for an index option.

    Args:
        symbol      : e.g. "NIFTY", "BANKNIFTY"
        strike      : e.g. "25250" or 25250
        option_type : "PE" or "CE"

    Returns:
        dict with security_id, trading_symbol, expiry, lot_size
        or None if not found.
    """
    today = pd.Timestamp.now().normalize()

    mask = (
        instruments["SEM_TRADING_SYMBOL"].str.upper().str.startswith(symbol.upper()) &
        (instruments["SEM_STRIKE_PRICE"] == float(strike)) &
        (instruments["SEM_OPTION_TYPE"].str.upper() == option_type.upper()) &
        (instruments["SEM_INSTRUMENT_NAME"] == "OPTIDX") &
        (instruments["_expiry_dt"] >= today)
    )

    filtered = instruments[mask]
    if filtered.empty:
        return None

    row = filtered.sort_values("_expiry_dt").iloc[0]
    return {
        "security_id":     str(int(row["SEM_SMST_SECURITY_ID"])),
        "trading_symbol":  row["SEM_TRADING_SYMBOL"],
        "expiry":          row["SEM_EXPIRY_DATE"],
        "lot_size":        int(row["SEM_LOT_UNITS"]),
        "exchange_segment": "NSE_FNO",
    }
