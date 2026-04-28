"""
POV (Pattern OI Velocity) — watches 6 option strikes live and fires a
short-squeeze signal when the 5-condition pattern fires on a 1-min candle.

State machine: idle → active → idle (Stop button or new Setup call resets it).
All state is module-level — same pattern as _tracker in oi_tracker.py.
"""

import logging
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify

import pov_engine

log = logging.getLogger(__name__)
bp  = Blueprint("pov", __name__)

# ── Module-level state ────────────────────────────────────────────────────────

_pov_state: dict = {
    "active":     False,
    "symbol":     None,
    "atm_strike": None,
    "strike_gap": None,
    "strikes":    [],   # [{strike, option_type, sid, label, exchange_segment}]
    "sids":       {},   # sid → {strike, option_type, label}
}

_candles:        dict = {}   # sid → list of completed 1-min candles (last 20)
_current_candle: dict = {}   # sid → in-progress candle for current minute
_signals:        dict = {}   # sid → latest result from pov_engine.evaluate
_signal_log:     list = []   # capped at 100, most recent at end


# ── Tick callback (runs in feed_manager dispatch thread) ──────────────────────

def _on_tick(sid: str, tick: dict) -> None:
    if sid not in _pov_state["sids"]:
        return

    ltp = float(tick.get("LTP") or tick.get("last_price") or 0)
    oi  = int(tick.get("OI")  or tick.get("oi")           or 0)
    ltq = int(tick.get("LTQ") or tick.get("last_traded_quantity") or 0)

    if ltp <= 0:
        return

    now        = datetime.now()
    minute_key = now.strftime("%Y-%m-%d %H:%M")
    cur        = _current_candle.get(sid)

    if cur is None:
        _current_candle[sid] = _open_candle(minute_key, ltp, ltq, oi, now)
        return

    if cur["minute"] != minute_key:
        _close_and_evaluate(sid, cur, now)
        _current_candle[sid] = _open_candle(minute_key, ltp, ltq, oi, now)
    else:
        cur["high"]   = max(cur["high"], ltp)
        cur["low"]    = min(cur["low"],  ltp)
        cur["close"]  = ltp
        cur["volume"] += ltq
        if oi > 0:
            cur["oi"] = oi


def _open_candle(minute_key: str, ltp: float, ltq: int, oi: int,
                 now: datetime) -> dict:
    return {
        "minute": minute_key,
        "open":   ltp,
        "high":   ltp,
        "low":    ltp,
        "close":  ltp,
        "volume": ltq,
        "oi":     oi,
        "time":   now.strftime("%H:%M"),
    }


def _close_and_evaluate(sid: str, completed_raw: dict, now: datetime) -> None:
    """Finalise a closed candle, append it, and run the pattern engine."""
    completed = dict(completed_raw)

    # Compute oi_change vs the previous completed candle
    prev_list = _candles.get(sid, [])
    completed["oi_change"] = (
        completed["oi"] - prev_list[-1]["oi"]
        if prev_list else 0
    )

    _candles.setdefault(sid, []).append(completed)
    if len(_candles[sid]) > 20:
        _candles[sid] = _candles[sid][-20:]

    is_midcp = (_pov_state.get("symbol") or "").upper() == "MIDCPNIFTY"
    result   = pov_engine.evaluate(sid, _candles[sid], is_midcp=is_midcp)
    _signals[sid] = result

    if result.get("is_new") and result.get("action") in ("STRONG", "WATCH"):
        info = _pov_state["sids"].get(sid, {})
        _signal_log.append({
            "time":        result.get("fired_at"),
            "strike":      info.get("strike"),
            "option_type": info.get("option_type"),
            "label":       info.get("label"),
            "score":       result.get("score"),
            "action":      result.get("action"),
            "entry":       result.get("entry"),
            "sl":          result.get("sl"),
            "t1":          result.get("t1"),
        })
        if len(_signal_log) > 100:
            _signal_log[:] = _signal_log[-100:]


# ── Routes ────────────────────────────────────────────────────────────────────

_STRIKE_GAPS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100,
}


@bp.route("/pov")
def pov_page():
    return render_template("pov.html", state=_pov_state)


@bp.route("/api/pov/atm_info")
def pov_atm_info():
    """
    Return nearest expiry + ATM strike + 6 strike labels for the given symbol.
    Used by the page on load and on symbol change to auto-populate the form.
    """
    from routes.analyzer import INDICES
    from dhan_broker import dhan
    import candle_service

    symbol = request.args.get("symbol", "NIFTY").upper()
    info   = INDICES.get(symbol)
    if not info:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 400

    gap = _STRIKE_GAPS.get(symbol, 50)

    # Spot price — last candle close, fallback to price_feed LTP
    spot = None
    candles = candle_service.get_candles(symbol, n=1)
    if candles:
        spot = float(candles[-1]["close"] or 0) or None

    if not spot:
        try:
            import price_feed
            spot = price_feed.get_ltp(str(info["security_id"]))
        except Exception:
            pass

    if not spot:
        return jsonify({"error": "Spot price unavailable — start the app during market hours or wait for candle data"}), 400

    atm = int(round(spot / gap) * gap)

    # Nearest expiry
    expiry = ""
    try:
        resp     = dhan.expiry_list(info["security_id"], dhan.INDEX)
        expiries = (resp.get("data") or {}).get("data") or []
        expiry   = expiries[0] if expiries else ""
    except Exception:
        pass

    strikes = [
        {"strike": atm - 2 * gap, "option_type": "CE", "label": f"{atm - 2*gap} CE"},
        {"strike": atm - 1 * gap, "option_type": "CE", "label": f"{atm - 1*gap} CE"},
        {"strike": atm,           "option_type": "CE", "label": f"{atm} CE"},
        {"strike": atm,           "option_type": "PE", "label": f"{atm} PE"},
        {"strike": atm + 1 * gap, "option_type": "PE", "label": f"{atm + 1*gap} PE"},
        {"strike": atm + 2 * gap, "option_type": "PE", "label": f"{atm + 2*gap} PE"},
    ]

    return jsonify({
        "symbol":     symbol,
        "spot":       round(spot, 2),
        "atm_strike": atm,
        "strike_gap": gap,
        "expiry":     expiry,
        "strikes":    strikes,
    })


@bp.route("/api/pov/setup", methods=["POST"])
def pov_setup():
    """
    Accept {symbol, expiry, atm_strike, strike_gap}.
    Resolve 6 security IDs via lookup_security, subscribe to feed_manager.
    """
    global _pov_state, _candles, _current_candle, _signals, _signal_log

    data       = request.get_json(force=True) or {}
    symbol     = str(data.get("symbol",     "NIFTY")).upper()
    atm_strike = int(data.get("atm_strike", 0))
    strike_gap = int(data.get("strike_gap", 50))

    if atm_strike <= 0:
        return jsonify({"error": "atm_strike must be a positive integer"}), 400

    from dhan_broker import lookup_security
    from dhanhq import MarketFeed

    # 3 CE strikes (at/below ATM) + 3 PE strikes (at/above ATM)
    strike_configs = [
        (atm_strike - 2 * strike_gap, "CE"),
        (atm_strike - 1 * strike_gap, "CE"),
        (atm_strike,                   "CE"),
        (atm_strike,                   "PE"),
        (atm_strike + 1 * strike_gap, "PE"),
        (atm_strike + 2 * strike_gap, "PE"),
    ]

    strikes_list  = []
    sids_map      = {}
    feed_instrs   = []

    for strike_val, opt_type in strike_configs:
        sec = lookup_security(symbol, strike_val, opt_type)
        if not sec:
            return jsonify({
                "error": f"No contract found: {symbol} {strike_val} {opt_type}"
            }), 400

        sid   = sec["security_id"]
        exch  = sec["exchange_segment"]
        label = f"{strike_val} {opt_type}"
        exch_mf = MarketFeed.BSE_FNO if exch == "BSE_FNO" else MarketFeed.NSE_FNO

        strikes_list.append({
            "strike":           strike_val,
            "option_type":      opt_type,
            "sid":              sid,
            "label":            label,
            "exchange_segment": exch,
            "expiry":           sec.get("expiry", ""),
        })
        sids_map[sid] = {
            "strike":      strike_val,
            "option_type": opt_type,
            "label":       label,
        }
        feed_instrs.append((exch_mf, sid, MarketFeed.Full))

    # Reset all per-session state
    _candles        = {}
    _current_candle = {}
    _signals        = {}
    _signal_log     = []

    _pov_state.update({
        "active":     True,
        "symbol":     symbol,
        "atm_strike": atm_strike,
        "strike_gap": strike_gap,
        "strikes":    strikes_list,
        "sids":       sids_map,
    })

    import feed_manager
    feed_manager.subscribe("pov", feed_instrs, on_tick=_on_tick)
    log.info("[pov] subscribed — %s ATM=%s gap=%s (%d instruments)",
             symbol, atm_strike, strike_gap, len(feed_instrs))

    return jsonify({
        "ok":      True,
        "symbol":  symbol,
        "strikes": [s["label"] for s in strikes_list],
        "expiry":  strikes_list[0]["expiry"] if strikes_list else "",
    })


@bp.route("/api/pov/stop", methods=["POST"])
def pov_stop():
    global _pov_state
    import feed_manager
    feed_manager.unsubscribe("pov")
    _pov_state.update({"active": False, "strikes": [], "sids": {}})
    log.info("[pov] unsubscribed")
    return jsonify({"ok": True})


@bp.route("/api/pov/status")
def pov_status():
    strikes_data = []
    for info in _pov_state.get("strikes", []):
        sid      = info["sid"]
        signal   = _signals.get(sid) or {"action": "WAIT", "score": 0,
                                          "c1": False, "c2": False,
                                          "c3": False, "c4": False,
                                          "c5": False,
                                          "entry": None, "sl": None,
                                          "t1": None, "t2": None, "t3": None,
                                          "fired_at": None, "is_new": False}
        cur_c    = _current_candle.get(sid) or {}
        hist     = _candles.get(sid, [])
        last_c   = hist[-1] if hist else {}
        n_candles = len(hist)

        strikes_data.append({
            "sid":           sid,
            "strike":        info["strike"],
            "option_type":   info["option_type"],
            "label":         info["label"],
            "expiry":        info.get("expiry", ""),
            "signal":        signal,
            "last_candle":   last_c,
            "current_candle": cur_c,
            "candle_count":  n_candles,
        })

    return jsonify({
        "active":     _pov_state.get("active", False),
        "symbol":     _pov_state.get("symbol"),
        "atm_strike": _pov_state.get("atm_strike"),
        "strikes":    strikes_data,
        "signal_log": list(reversed(_signal_log[-20:])),
    })
