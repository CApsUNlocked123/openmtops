# ── Dummy security (what lookup_security returns) ─────────────────────────────
SECURITY = {
    "security_id":      "49546",
    "trading_symbol":   "NIFTY25100CE",
    "expiry":           "2025-05-29",
    "lot_size":         75,
    "exchange_segment": "NSE_FNO",
}

FUND_LIMITS = {"status": "success", "data": {"availabelBalance": "500000.00"}}
ORDER_RESP  = {"status": "success", "data": {"orderId": "TEST-ORDER-001"}}
POSITIONS   = {"status": "success", "data": []}

# ── Price walk: entry=100, sl=90, T1=115, T2=130 ──────────────────────────────
# Below entry (watching) → crosses entry range (buy) → trail → T1 partial → T2 exit
TICK_ENTRY = 100.0
TICK_SL    = 90.0
TICK_T1    = 115.0
TICK_T2    = 130.0

PRICE_WALK = [
    93.0, 95.0, 97.0, 99.0,   # watching — below entry, no buy yet
    102.0,                     # entry(100) < 102 < buy_mid(107.5) → BUY triggers
    104.0, 106.0,              # active — no trail yet (< half_mark 108)
    109.0,                     # >= half_mark(108) → SL trails from 90 → 104.5
    111.0, 113.0,
    116.0,                     # >= T1(115) → partial exit, SL → 108, t1_hit=True
    120.0, 124.0, 127.0,
    131.0,                     # >= T2(130) → full exit, trade_done emitted
]
TICK_INTERVAL_S = 1.5         # seconds between each price step

# ── Real NIFTY 5-min intraday candles — loaded from fakedata.json ─────────────
# Source: Dhan intraday_minute_data API — native array format [ts, o, h, l, c, vol, oi]
# ETL matches exactly what production candle_service stores and returns from SQLite.
# Incomplete rows (len < 5) are skipped — file may be truncated.
import json as _json, os as _os
_JSON_PATH = _os.path.join(_os.path.dirname(__file__), "..", "fakedata.json")
with open(_JSON_PATH) as _f:
    _raw_text = _f.read()

# Parse only complete candle entries — robust against truncated file
import re as _re
_RAW = [_json.loads(m) for m in _re.findall(
    r'\[\s*"20\d\d-[^"]+"\s*(?:,\s*[\d.]+){4,}\s*\]', _raw_text
) if len(_json.loads(m)) >= 5]

# ETL to production candle_service format: {"time": "YYYY-MM-DD HH:MM:SS", "open": float, ...}
# Strips timezone suffix, replaces T separator — identical to what SQLite stores and returns.
def _ts(raw):
    t = raw[0]
    t = t.split("+")[0]   # strip +0530
    return t.replace("T", " ")

CANDLES = [
    {
        "time":   _ts(r),
        "open":   float(r[1]),
        "high":   float(r[2]),
        "low":    float(r[3]),
        "close":  float(r[4]),
        "volume": int(r[5]) if len(r) > 5 else 0,
    }
    for r in _RAW
]

# Live partial candle — continuing from the last completed bar
_last = CANDLES[-1] if CANDLES else {}
LIVE_CANDLE = {
    "time":            _last.get("time", "2026-04-02 15:30:00"),
    "open":            _last.get("close", 0),
    "high":            _last.get("high",  0),
    "low":             _last.get("low",   0),
    "close":           _last.get("close", 0),
    "volume":          5_000,
    "partial":         True,
    "minutes_elapsed": 3,
}

# ── Dummy OI snapshot (mirrors _compute_kpis return value from oi_tracker) ────
# ATM = nearest 50-strike to last candle close, derived from loaded data
_last_close = CANDLES[-1]["close"] if CANDLES else 22700
ATM = round(_last_close / 50) * 50


def _make_oi_rows():
    rows = []
    for off in range(-5, 6):
        s    = ATM + off * 50
        ce_b = 1_200_000 + off * 50_000
        pe_b = max(200_000, 1_000_000 - off * 40_000)
        rows.append({
            "strike":     s,
            "ce_oi":      ce_b + 80_000,
            "ce_oi_base": ce_b,
            "ce_delta":   80_000,
            "ce_pct":     round(80_000 / ce_b * 100, 2),
            "ce_ltp":     max(5.0, (ATM - s) * 0.3 + 50),
            "ce_pattern": "Short Buildup",
            "pe_oi":      pe_b + 60_000,
            "pe_oi_base": pe_b,
            "pe_delta":   60_000,
            "pe_pct":     round(60_000 / pe_b * 100, 2),
            "pe_ltp":     max(5.0, (s - ATM) * 0.3 + 50),
            "pe_pattern": "Long Buildup",
        })
    return rows


OI_SNAPSHOT = {
    "state":          "tracking",
    "start_time":     "2025-04-04T09:15:00",
    "start_display":  "2025-04-04 09:15:00",
    "duration":       "45m 0s",
    "pcr_base":       0.92,
    "pcr_now":        0.97,
    "pcr_change":     0.05,
    "total_ce_delta": 880_000,
    "total_pe_delta": 660_000,
    "rows":           _make_oi_rows(),
    "large_orders":   [],
    "avg_ce_iv":      12.5,
    "avg_pe_iv":      13.2,
    "iv_skew_ratio":  1.056,
    "iv_skew_label":  "PUT SKEW — fear of downside",
    "straddle_cost":  210.5,
    "straddle_pct":   0.85,
    "atm_strike":     ATM,
    "ultp":           _last_close,
}
