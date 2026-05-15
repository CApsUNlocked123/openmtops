"""
Option Analyzer — fetches 1-min time series (price, OI, volume) for nearest
5 strikes around ATM and reconstructs IV per minute via Black-Scholes.

Flow:
  1. /analyzer/expiries  → list expiries for selected index
  2. /analyzer/series    → resolve nearest 5 strikes around ATM, then fetch
                           1-min OHLCV+OI for each CE/PE leg + the underlying,
                           compute IV per minute, return everything.
"""

import time
from datetime import date, datetime
from math import log, sqrt, exp, erf, pi

from flask import Blueprint, render_template, request, jsonify

from dhan_broker import dhan

bp = Blueprint("analyzer", __name__)

INDICES = {
    "NIFTY":      {"security_id": 13,  "lot_size": 65,  "exchange": "NSE_FNO", "spot_exch": "IDX_I"},
    "BANKNIFTY":  {"security_id": 25,  "lot_size": 30,  "exchange": "NSE_FNO", "spot_exch": "IDX_I"},
    "FINNIFTY":   {"security_id": 27,  "lot_size": 60,  "exchange": "NSE_FNO", "spot_exch": "IDX_I"},
    "MIDCPNIFTY": {"security_id": 442, "lot_size": 120, "exchange": "NSE_FNO", "spot_exch": "IDX_I"},
    "SENSEX":     {"security_id": 51,  "lot_size": 10,  "exchange": "BSE_FNO", "spot_exch": "IDX_I"},
}

RISK_FREE_RATE = 0.065   # 6.5% — close enough for short-dated IV reconstruction
N_STRIKES      = 7       # 3 below + ATM + 3 above


def register_socketio(sio):
    pass   # no live feed in the rebuilt analyzer


# ── Black-Scholes & IV solver ────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def _bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    if is_call:
        return S * _norm_cdf(d1) - K * exp(-r * T) * _norm_cdf(d2)
    return K * exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    return S * _norm_pdf(d1) * sqrt(T)


def implied_vol(price: float, S: float, K: float, T: float, r: float, is_call: bool) -> float | None:
    """Newton-Raphson with bisection fallback. Returns IV as decimal (0.18 = 18%)."""
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if price < intrinsic - 0.5:
        return None

    sigma = 0.3
    for _ in range(60):
        diff = _bs_price(S, K, T, r, sigma, is_call) - price
        if abs(diff) < 1e-4:
            return sigma
        v = _bs_vega(S, K, T, r, sigma)
        if v < 1e-8:
            break
        sigma -= diff / v
        if sigma <= 0 or sigma > 5:
            break

    lo, hi = 1e-4, 5.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _bs_price(S, K, T, r, mid, is_call) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-4:
            return mid
    return None


# ── Dhan helpers ──────────────────────────────────────────────────────────────

def _fetch_minute_series(security_id: str, exchange: str, instrument: str, oi: bool):
    """Returns dict with parallel arrays: timestamps, close, volume, oi.
    Returns None on rate-limit / API failure / non-dict response."""
    today = date.today().strftime("%Y-%m-%d")
    try:
        resp = dhan.intraday_minute_data(
            security_id, exchange, instrument, today, today, interval=1, oi=oi,
        )
    except Exception:
        return None
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return None
    d = resp.get("data") or {}
    times = d.get("timestamp") or d.get("start_Time") or []
    # Dhan returns OI under the key `open_interest`. Older key candidates
    # (`OI` / `oi`) kept as fallback in case the API surface shifts.
    oi_raw = d.get("open_interest") or d.get("OI") or d.get("oi") or []
    return {
        "timestamps": [int(t) for t in times],
        "open":       [float(x) for x in (d.get("open")   or [])],
        "high":       [float(x) for x in (d.get("high")   or [])],
        "low":        [float(x) for x in (d.get("low")    or [])],
        "close":      [float(x) for x in (d.get("close")  or [])],
        "volume":     [int(x)   for x in (d.get("volume") or [])],
        "oi":         [int(x)   for x in oi_raw],
    }


# ── Diagnostics ──────────────────────────────────────────────────────────────
# All four functions operate on the per-leg series dict (timestamps/price/oi/
# volume/iv) and return derived series of the same length, padded with None at
# the front where insufficient history exists. Precision rules:
#   • OI-delta classifier uses minimum thresholds so noise doesn't get labeled.
#   • IV-stability uses rolling stdev with a configurable window.
#   • Volume-divergence requires BOTH a meaningful price move AND a volume drop.
# ─────────────────────────────────────────────────────────────────────────────

# Thresholds (tuned conservatively to avoid false positives on quiet ticks)
OI_MIN_DELTA_PCT     = 0.5    # % change in OI to be considered "meaningful"
PRICE_MIN_DELTA_PCT  = 0.3    # % change in option price to qualify direction
IV_WINDOW            = 15     # rolling window for IV stability (minutes)
IV_STABLE_STDEV_PCT  = 1.0    # IV stdev below this = STABLE (absolute IV points)
IV_DRIFT_RATIO       = 0.7    # |mean drift| / window-range above this = directional
VOL_DIV_WINDOW       = 15     # rolling window for volume-divergence median
VOL_DIV_PRICE_PCT    = 0.5    # min |Δprice| to consider a "move"
VOL_DIV_VOLUME_RATIO = 0.4    # volume below this fraction of rolling median = divergent


def _oi_label(d_price: float, d_oi: float) -> str | None:
    """Classify one minute's (Δprice, ΔOI) into a position-action label."""
    if d_oi == 0 and d_price == 0:
        return "NEUTRAL"
    # Treat sub-threshold deltas as "no signal" rather than mislabeling
    if abs(d_oi) < OI_MIN_DELTA_PCT and abs(d_price) < PRICE_MIN_DELTA_PCT:
        return "NEUTRAL"
    if d_oi > 0 and d_price > 0:  return "LONG_BUILDUP"
    if d_oi > 0 and d_price < 0:  return "SHORT_BUILDUP"
    if d_oi < 0 and d_price > 0:  return "SHORT_COVERING"
    if d_oi < 0 and d_price < 0:  return "LONG_UNWINDING"
    return "NEUTRAL"


def classify_oi_deltas(prices: list[float], ois: list[int]) -> list[str | None]:
    """Per-minute OI action labels. Index 0 is None (no prior bar to diff against)."""
    out: list[str | None] = [None]
    for i in range(1, len(prices)):
        p_prev, p_now = prices[i-1], prices[i]
        o_prev, o_now = ois[i-1], ois[i]
        if p_prev <= 0 or o_prev <= 0:
            out.append(None)
            continue
        dp_pct = (p_now - p_prev) / p_prev * 100.0
        do_pct = (o_now - o_prev) / o_prev * 100.0
        out.append(_oi_label(dp_pct, do_pct))
    return out


def summarize_oi_labels(labels: list[str | None]) -> dict:
    """Tally label counts and pick the dominant action across the session."""
    counts: dict[str, int] = {}
    for lab in labels:
        if not lab or lab == "NEUTRAL":
            continue
        counts[lab] = counts.get(lab, 0) + 1
    if not counts:
        return {"dominant": "NEUTRAL", "counts": {}}
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    return {"dominant": dominant, "counts": counts}


def iv_stability(ivs: list[float | None], window: int = IV_WINDOW) -> dict:
    """Classify the last `window` minutes of IV as STABLE / RISING / FALLING /
    FLUCTUATING. Returns dict with state, mean, stdev. None if insufficient data."""
    recent = [v for v in ivs[-window:] if v is not None]
    if len(recent) < max(3, window // 3):
        return {"state": "UNKNOWN", "mean": None, "stdev": None, "n": len(recent)}

    n      = len(recent)
    mean   = sum(recent) / n
    var    = sum((v - mean) ** 2 for v in recent) / n
    stdev  = var ** 0.5
    rng    = max(recent) - min(recent)

    if stdev < IV_STABLE_STDEV_PCT:
        state = "STABLE"
    else:
        # Compare endpoints: if movement is mostly directional, label rising/falling
        first_third = sum(recent[:max(1, n // 3)]) / max(1, n // 3)
        last_third  = sum(recent[-max(1, n // 3):]) / max(1, n // 3)
        drift = last_third - first_third
        if rng > 0 and abs(drift) / rng >= IV_DRIFT_RATIO:
            state = "RISING" if drift > 0 else "FALLING"
        else:
            state = "FLUCTUATING"

    return {
        "state": state,
        "mean":  round(mean, 2),
        "stdev": round(stdev, 3),
        "n":     n,
    }


def volume_divergence(prices: list[float], volumes: list[int],
                      window: int = VOL_DIV_WINDOW) -> dict:
    """Count minutes where price moved meaningfully but volume was well below
    its rolling median. High count → moves are unsupported by participation.
    Also returns the most-recent flag for "is the latest bar divergent?". """
    if len(prices) < window + 2:
        return {"count": 0, "latest": False, "n_bars": 0}

    flags = 0
    latest = False
    n_checked = 0
    for i in range(window, len(prices)):
        p_prev, p_now = prices[i-1], prices[i]
        if p_prev <= 0:
            continue
        dp_pct = abs(p_now - p_prev) / p_prev * 100.0
        if dp_pct < VOL_DIV_PRICE_PCT:
            continue
        recent_vols = [v for v in volumes[i-window:i] if v > 0]
        if len(recent_vols) < window // 2:
            continue
        median = sorted(recent_vols)[len(recent_vols) // 2]
        if median <= 0:
            continue
        n_checked += 1
        is_divergent = volumes[i] < VOL_DIV_VOLUME_RATIO * median
        if is_divergent:
            flags += 1
            if i == len(prices) - 1:
                latest = True
    return {"count": flags, "latest": latest, "n_bars": n_checked}


def compute_max_pain(oi_by_strike: dict[int, dict]) -> int | None:
    """Strike that minimizes total writer payout at expiry.
    `oi_by_strike` maps strike → {"ce_oi": int, "pe_oi": int}."""
    strikes = sorted(oi_by_strike.keys())
    if not strikes:
        return None
    best_strike = None
    best_pain   = float("inf")
    for candidate in strikes:
        pain = 0.0
        for k in strikes:
            v = oi_by_strike[k]
            pain += max(0, candidate - k) * v["ce_oi"]   # ITM calls
            pain += max(0, k - candidate) * v["pe_oi"]   # ITM puts
        if pain < best_pain:
            best_pain   = pain
            best_strike = candidate
    return best_strike


def compute_neutral_zone(oi_by_strike: dict[int, dict]) -> int | None:
    """Strike with the highest combined CE+PE OI — the dominant magnet/wall."""
    if not oi_by_strike:
        return None
    return max(oi_by_strike.items(),
               key=lambda kv: kv[1]["ce_oi"] + kv[1]["pe_oi"])[0]


# ── Strike selection ────────────────────────────────────────────────────────

def _nearest_strikes(oc: dict, spot: float, n: int):
    """Pick `n` strikes closest to spot. Returns sorted list of (strike_int, ce_sid, pe_sid)."""
    items = []
    for strike_str, data in oc.items():
        try:
            strike = int(float(strike_str))
        except (TypeError, ValueError):
            continue
        ce_sid = ((data.get("ce") or {}).get("security_id"))
        pe_sid = ((data.get("pe") or {}).get("security_id"))
        if not ce_sid or not pe_sid:
            continue
        items.append((strike, str(int(ce_sid)), str(int(pe_sid))))
    items.sort(key=lambda x: abs(x[0] - spot))
    chosen = sorted(items[:n], key=lambda x: x[0])
    return chosen


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/analyzer")
def analyzer_page():
    instrument = request.args.get("instrument", "NIFTY")
    if instrument not in INDICES:
        instrument = "NIFTY"
    return render_template(
        "analyzer.html",
        indices=list(INDICES.keys()),
        selected_instrument=instrument,
    )


@bp.route("/analyzer/expiries", methods=["POST"])
def load_expiries():
    instrument = (request.json or {}).get("instrument", "NIFTY")
    info = INDICES.get(instrument)
    if not info:
        return jsonify({"error": "Unknown instrument"}), 400
    try:
        resp = dhan.expiry_list(info["security_id"], dhan.INDEX)
        exps = resp["data"]["data"] if resp.get("status") == "success" else []
        return jsonify({"expiries": exps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_series_payload(instrument: str, expiry: str):
    """Core data-fetch + per-strike computations. Returns (payload_dict, None)
    on success, or (None, (error_msg, http_status)) on failure. Shared between
    /analyzer/series and the advanced narrative endpoint."""
    info = INDICES.get(instrument)
    if not info or not expiry:
        return None, ("Missing params", 400)

    try:
        raw = dhan.option_chain(info["security_id"], dhan.INDEX, expiry)
        if raw.get("status") != "success":
            return None, (f"option_chain failed: {raw.get('remarks', raw)}", 500)
        inner = raw["data"]["data"]
        oc    = inner["oc"]
        spot  = float(inner.get("last_price") or 0)
    except Exception as e:
        return None, (f"option_chain error: {e}", 500)

    if not spot or not oc:
        return None, ("Empty option chain or zero spot", 500)

    strikes = _nearest_strikes(oc, spot, N_STRIKES)
    if len(strikes) < N_STRIKES:
        return None, (f"Only found {len(strikes)} usable strikes near {spot}", 500)

    spot_series = _fetch_minute_series(
        str(info["security_id"]), info["spot_exch"], "INDEX", oi=False,
    )
    if not spot_series or not spot_series["timestamps"]:
        return None, ("No spot intraday data (market closed?)", 500)

    ts_to_spot = dict(zip(spot_series["timestamps"], spot_series["close"]))

    try:
        expiry_dt = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
    except ValueError:
        expiry_dt = date.today()
    days_left = max(1, (expiry_dt - date.today()).days)
    T_base    = days_left / 365.0

    # Dhan caps the intraday endpoint at ~5 req/sec. We fire 1 (spot) + 14
    # (7 strikes × CE/PE) requests; a ~250ms pacing keeps us under the limit
    # and avoids rate-limit None responses.
    PACING_S = 0.25

    out_strikes = []
    latest_oi_snapshot: dict[int, dict] = {}
    for strike, ce_sid, pe_sid in strikes:
        leg_out = {"strike": strike, "ce_sid": ce_sid, "pe_sid": pe_sid}
        snapshot = {"ce_oi": 0, "pe_oi": 0}
        for side, sid in (("ce", ce_sid), ("pe", pe_sid)):
            time.sleep(PACING_S)
            series = _fetch_minute_series(sid, info["exchange"], "OPTIDX", oi=True)
            if not series or not series["timestamps"]:
                leg_out[side] = {
                    "timestamps": [], "price": [], "oi": [], "volume": [], "iv": [],
                    "oi_labels": [], "oi_summary": {"dominant": "NEUTRAL", "counts": {}},
                    "iv_state":  {"state": "UNKNOWN", "mean": None, "stdev": None, "n": 0},
                    "vol_div":   {"count": 0, "latest": False, "n_bars": 0},
                }
                continue

            ivs = []
            for t, close in zip(series["timestamps"], series["close"]):
                S = ts_to_spot.get(t)
                if S is None:
                    ivs.append(None)
                    continue
                iv = implied_vol(close, S, float(strike), T_base, RISK_FREE_RATE, side == "ce")
                ivs.append(round(iv * 100, 2) if iv is not None else None)

            oi_labels  = classify_oi_deltas(series["close"], series["oi"])
            oi_summary = summarize_oi_labels(oi_labels)
            iv_state   = iv_stability(ivs)
            vol_div    = volume_divergence(series["close"], series["volume"])

            for oi_val in reversed(series["oi"]):
                if oi_val > 0:
                    snapshot[f"{side}_oi"] = oi_val
                    break

            leg_out[side] = {
                "timestamps": series["timestamps"],
                "price":      series["close"],
                "oi":         series["oi"],
                "volume":     series["volume"],
                "iv":         ivs,
                "oi_labels":  oi_labels,
                "oi_summary": oi_summary,
                "iv_state":   iv_state,
                "vol_div":    vol_div,
            }
        out_strikes.append(leg_out)
        latest_oi_snapshot[strike] = snapshot

    payload = {
        "instrument":   instrument,
        "expiry":       expiry,
        "spot":         spot,
        "days_left":    days_left,
        "max_pain":     compute_max_pain(latest_oi_snapshot),
        "neutral_zone": compute_neutral_zone(latest_oi_snapshot),
        "spot_series":  {
            "timestamps": spot_series["timestamps"],
            "close":      spot_series["close"],
        },
        "strikes":      out_strikes,
    }
    return payload, None


@bp.route("/analyzer/series", methods=["POST"])
def load_series():
    req        = request.json or {}
    instrument = req.get("instrument", "NIFTY")
    expiry     = req.get("expiry", "")
    payload, err = _build_series_payload(instrument, expiry)
    if err:
        msg, status = err
        return jsonify({"error": msg}), status
    return jsonify(payload)


@bp.route("/analyzer/advanced")
def analyzer_advanced_page():
    instrument = request.args.get("instrument", "NIFTY")
    if instrument not in INDICES:
        instrument = "NIFTY"
    return render_template(
        "analyzer_advanced.html",
        indices=list(INDICES.keys()),
        selected_instrument=instrument,
    )


@bp.route("/analyzer/advanced/data", methods=["POST"])
def load_advanced():
    """Same as /analyzer/series but also runs the narrative generator and
    attaches the bullets array."""
    import narrative
    req        = request.json or {}
    instrument = req.get("instrument", "NIFTY")
    expiry     = req.get("expiry", "")
    payload, err = _build_series_payload(instrument, expiry)
    if err:
        msg, status = err
        return jsonify({"error": msg}), status
    payload["narrative"] = narrative.generate(payload)
    return jsonify(payload)
