"""
Dhan broker integration — SDK client + instrument master lookup.

Initialization is lazy: the DhanHQ SDK and scrip master CSV are loaded
on first use, not at import time.  This lets the app start and display
the setup wizard even before credentials are configured.

Call reset_dhan() after saving new credentials (e.g. from the Settings
page) to force re-initialization on the next request without a restart.
"""

import os
import pandas as pd
from dhanhq import DhanContext, dhanhq
from runtime_config import get_dhan_credentials

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Module-level references — None until first use
dhan_context    = None
dhan            = None
instruments     = None
_init_client_id = None   # tracks which credentials we last built with


def _ensure_initialized() -> None:
    """Initialize (or re-initialize) the Dhan SDK if credentials have changed."""
    global dhan_context, dhan, instruments, _init_client_id

    client_id, token = get_dhan_credentials()

    if dhan is None or _init_client_id != client_id:
        dhan_context    = DhanContext(client_id, token)
        dhan            = dhanhq(dhan_context)
        _init_client_id = client_id

    if instruments is None:
        instruments = pd.read_csv(SCRIP_MASTER_URL, low_memory=False)
        instruments["_expiry_dt"] = pd.to_datetime(
            instruments["SEM_EXPIRY_DATE"], errors="coerce"
        )


def reset_dhan() -> None:
    """
    Force re-initialization on next use.
    Call this after saving new Dhan credentials via the Settings page
    so the new token takes effect without restarting the process.
    """
    global _init_client_id
    _init_client_id = None


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
    _ensure_initialized()

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
    exch = "BSE_FNO" if str(row.get("SEM_EXM_EXCH_ID", "")).upper() == "BSE" else "NSE_FNO"
    return {
        "security_id":      str(int(row["SEM_SMST_SECURITY_ID"])),
        "trading_symbol":   row["SEM_TRADING_SYMBOL"],
        "expiry":           row["SEM_EXPIRY_DATE"],
        "lot_size":         int(row["SEM_LOT_UNITS"]),
        "exchange_segment": exch,
    }


# ── Proxy objects for backward compatibility ──────────────────────────────────
# Routes do `from dhan_broker import dhan, dhan_context`.
# These proxy classes forward all attribute access to the lazily-initialized
# real objects so existing code needs no changes.

class _DhanProxy:
    def __getattr__(self, name):
        _ensure_initialized()
        return getattr(dhan, name)

    def __repr__(self):
        return f"<DhanProxy init={'yes' if dhan else 'no'}>"


class _ContextProxy:
    def __getattr__(self, name):
        _ensure_initialized()
        return getattr(dhan_context, name)

    def __repr__(self):
        return f"<ContextProxy init={'yes' if dhan_context else 'no'}>"


# Replace module-level names with proxy instances.
# Code that does `from dhan_broker import dhan` gets the proxy,
# and `dhan.get_positions()` transparently calls _ensure_initialized() first.
dhan         = _DhanProxy()
dhan_context = _ContextProxy()
