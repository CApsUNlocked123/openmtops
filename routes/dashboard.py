"""
Strategy Dashboard routes.

Three routes:
  GET /dashboard              — page render
  GET /api/dashboard/snapshot — full indicator JSON (5s poll)
  GET /api/dashboard/oi_map   — OI wall data (30s poll)

The dashboard is fully independent: reads candles from SQLite via
candle_service and OI data optionally from oi_tracker if it is running.
"""

from __future__ import annotations

from datetime import datetime
from flask import Blueprint, render_template, request, jsonify

import globals as g
import candle_service
from candle_service import INSTRUMENT_NAMES
from indicators_dashboard import (
    classify_regime,
    compute_move_velocity,
    classify_move_phase,
    compute_trend_health,
    compute_linear_move_score,
    detect_oi_wall,
    build_phase_timeline,
)

bp = Blueprint("dashboard", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Page route
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/dashboard")
def dashboard_page():
    return render_template("dashboard.html", instruments=INSTRUMENT_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_oi_snapshot() -> dict | None:
    """Return OI KPI dict from oi_tracker if it is currently tracking, else None."""
    try:
        from routes.oi_tracker import _compute_kpis, _tracker
        if _tracker.get("state") != "tracking":
            return None
        return _compute_kpis()
    except Exception:
        return None


def _compute_ema(candles: list[dict], period: int = 9) -> list[float | None]:
    """Standard EMA of close prices. Returns None for early bars."""
    if not candles:
        return []
    k      = 2.0 / (period + 1)
    result: list[float | None] = [None] * len(candles)
    ema    = None
    for i, c in enumerate(candles):
        close = c["close"]
        if close is None:
            result[i] = ema
            continue
        if ema is None:
            ema = close
        else:
            ema = close * k + ema * (1 - k)
        result[i] = round(ema, 2) if i >= period - 1 else None
    return result


def _compute_oi_direction(oi_rows: list[dict]) -> float:
    """
    Net OI directional bias from oi_tracker rows.
    +100 = heavy put buildup (bullish), -100 = heavy call buildup (bearish).
    """
    if not oi_rows:
        return 0.0
    net = sum(r.get("pe_delta", 0) - r.get("ce_delta", 0) for r in oi_rows)
    total = sum(abs(r.get("pe_delta", 0)) + abs(r.get("ce_delta", 0)) for r in oi_rows)
    if total == 0:
        return 0.0
    return round(max(-100.0, min(100.0, net / total * 100)), 2)


def _compute_iv_percentile(oi_snap: dict | None) -> float:
    """
    Map iv_skew_ratio to a 0–100 percentile.
    Low IV ratio (0.9) → 10 (buyers' market), High (1.1+) → 90 (sellers' market).
    """
    if not oi_snap:
        return 50.0
    ratio = oi_snap.get("iv_skew_ratio", 1.0) or 1.0
    # Map 0.9–1.1 range linearly to 0–100
    mapped = (ratio - 0.9) / 0.2 * 100
    return round(max(0.0, min(100.0, mapped)), 1)


def _compute_volume_ratio(candles: list[dict]) -> float:
    """Last candle volume / mean of prior 20 volumes, or 1.0 if no data."""
    if len(candles) < 2:
        return 1.0
    vols = [c["volume"] for c in candles if c.get("volume") is not None]
    if not vols or all(v == 0 for v in vols):
        return 1.0
    last_vol  = vols[-1]
    prior     = vols[-21:-1] if len(vols) > 1 else vols[:1]
    prior_avg = sum(prior) / len(prior) if prior else 1
    if prior_avg == 0:
        return 1.0
    return round(last_vol / prior_avg, 3)


def _compute_candle_structure(candles: list[dict]) -> float:
    """
    Candle structure quality score (0–100) for the last 4 candles.
    High body-to-range ratio = trending candles = higher score.
    """
    last4 = candles[-4:] if len(candles) >= 4 else candles
    if not last4:
        return 50.0
    scores = []
    for c in last4:
        r = c["high"] - c["low"]
        b = abs(c["close"] - c["open"])
        ratio = b / r if r > 0 else 0
        scores.append(ratio)
    avg = sum(scores) / len(scores)
    return round(avg * 100, 1)


def _update_phase_log(phase: str) -> None:
    """
    Append to globals.PHASE_LOG when the phase changes.
    Resets the log at the start of each trading day.
    """
    now     = datetime.now()
    now_str = now.strftime("%H:%M")
    today   = now.strftime("%Y-%m-%d")

    # Reset log if it's a new day
    if g.PHASE_LOG and g.PHASE_LOG[0].get("date") != today:
        g.PHASE_LOG.clear()

    if not g.PHASE_LOG:
        g.PHASE_LOG.append({"phase": phase, "start_time": now_str, "end_time": None, "date": today})
        return

    last = g.PHASE_LOG[-1]
    if last["phase"] != phase:
        last["end_time"] = now_str
        g.PHASE_LOG.append({"phase": phase, "start_time": now_str, "end_time": None, "date": today})


def _build_snapshot(instrument: str) -> dict:
    """Run all 7 indicator functions and return the full snapshot dict."""
    candles = candle_service.get_candles(instrument, n=50)

    # On-demand fetch if DB has no candles yet — only during market hours
    # (outside market hours the API returns DH-905; rely on DB from last session)
    if not candles:
        fetched = candle_service.fetch_instrument(instrument)   # no-op outside market hours
        if fetched:
            candles = candle_service.get_candles(instrument, n=50)

    # Append the current partial 5-min candle so indicators use the latest price
    live_candle = candle_service.get_live_candle(instrument)
    candles_all = candles + ([live_candle] if live_candle else [])

    if len(candles_all) < 6:
        return {
            "active": True, "ready": False,
            "candle_count": len(candles),
            "instrument": instrument,
        }

    # Use candles_all (includes live partial bar) for indicators — latest close is more accurate
    candles_12 = candles_all[-12:] if len(candles_all) >= 12 else candles_all
    candles_6  = candles_all[-6:]
    ema_all    = _compute_ema(candles_all, period=9)
    ema_12     = ema_all[-12:] if len(ema_all) >= 12 else ema_all

    regime   = classify_regime(candles_12, ema_12)
    velocity = compute_move_velocity(candles_6)

    # Optional OI data
    oi_snap       = _get_oi_snapshot()
    call_oi_delta = oi_snap["total_ce_delta"] if oi_snap else 0
    put_oi_delta  = oi_snap["total_pe_delta"]  if oi_snap else 0

    # Update PCR series for trend health
    if oi_snap and oi_snap.get("pcr_now"):
        g.PCR_SERIES.append(oi_snap["pcr_now"])
        if len(g.PCR_SERIES) > 100:
            g.PCR_SERIES.pop(0)

    pcr_series = list(g.PCR_SERIES[-20:])
    oi_history = ([{"total_ce_delta": call_oi_delta, "total_pe_delta": put_oi_delta}]
                  if oi_snap else [])

    phase = classify_move_phase(candles_12, ema_12, oi_history, regime)
    _update_phase_log(phase)

    health = compute_trend_health(
        candles_12, ema_12, call_oi_delta, put_oi_delta, pcr_series
    )

    oi_rows        = oi_snap["rows"]   if oi_snap else []
    oi_direction   = _compute_oi_direction(oi_rows)
    iv_percentile  = _compute_iv_percentile(oi_snap)
    vol_ratio      = _compute_volume_ratio(candles_all)
    struct_score   = _compute_candle_structure(candles_12)
    pcr_now        = oi_snap["pcr_now"] if oi_snap else 1.0

    linear   = compute_linear_move_score(
        regime, velocity, pcr_now, iv_percentile,
        oi_direction, vol_ratio, struct_score
    )

    # Phase log without internal "date" key for the frontend
    clean_log = [
        {"phase": e["phase"], "start_time": e["start_time"], "end_time": e.get("end_time")}
        for e in g.PHASE_LOG
    ]
    timeline = build_phase_timeline(clean_log, candles_12)
    spot     = candles_all[-1]["close"] if candles_all else None

    # Candle series for the phase line chart (all stored candles, not just last 12)
    candles_chart = [
        {"t": c["time"][11:16], "c": c["close"]}   # "HH:MM" label + close price
        for c in candles_all if c.get("close") is not None
    ]
    ema_chart = [
        round(v, 2) if v is not None else None
        for v in ema_all
    ]

    return {
        "active":        True,
        "ready":         True,
        "instrument":    instrument,
        "candle_count":  len(candles),          # completed candles only
        "spot":          spot,
        "regime":        regime,
        "velocity":      velocity,
        "phase":         phase,
        "trend_health":  health,
        "linear_score":  linear,
        "timeline":      timeline,
        "candles_chart": candles_chart,
        "ema_chart":     ema_chart,
        "pcr_now":       pcr_now,
        "oi_available":  oi_snap is not None,
        "atm_strike":    oi_snap.get("atm_strike") if oi_snap else None,
        "live_candle":   live_candle,
    }


# ─────────────────────────────────────────────────────────────────────────────
# API routes
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/api/dashboard/snapshot")
def snapshot():
    instrument = request.args.get("instrument", "NIFTY").upper()
    if instrument not in INSTRUMENT_NAMES:
        instrument = INSTRUMENT_NAMES[0]
    return jsonify(_build_snapshot(instrument))


@bp.route("/api/dashboard/start_oi", methods=["POST"])
def start_oi():
    """Auto-start OI tracking for the dashboard instrument (ATM±5 strikes)."""
    instrument = (request.json or {}).get("instrument", "NIFTY").upper()
    if instrument not in INSTRUMENT_NAMES:
        instrument = INSTRUMENT_NAMES[0]
    try:
        from routes.oi_tracker import start_for_instrument
        result = start_for_instrument(instrument)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/dashboard/oi_map")
def oi_map():
    instrument = request.args.get("instrument", "NIFTY").upper()
    oi_snap    = _get_oi_snapshot()

    if not oi_snap:
        return jsonify({"available": False})

    rows    = oi_snap.get("rows", [])
    strikes = [r["strike"] for r in rows]
    ce_oi   = [r["ce_oi"]  for r in rows]
    pe_oi   = [r["pe_oi"]  for r in rows]

    # Build strike_oi_map for wall detection
    strike_oi_map = {r["strike"]: {"ce_oi": r["ce_oi"], "pe_oi": r["pe_oi"]} for r in rows}
    spot = oi_snap.get("ultp") or (candle_service.get_candles(instrument, n=1) or [{}])[-1].get("close")

    wall = detect_oi_wall(strike_oi_map, spot or 0)

    from indicators import classify_pcr
    pcr_now   = oi_snap.get("pcr_now", 1.0)
    pcr_label = classify_pcr(pcr_now)

    return jsonify({
        "available":   True,
        "strikes":     strikes,
        "ce_oi":       ce_oi,
        "pe_oi":       pe_oi,
        "atm_strike":  oi_snap.get("atm_strike"),
        "wall":        wall,
        "pcr_now":     pcr_now,
        "pcr_label":   pcr_label,
    })
