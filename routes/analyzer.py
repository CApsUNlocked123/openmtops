"""
Option Analyzer route — loads option chain and streams live OI/LTP via SocketIO.
Chain data is stored server-side (module-level) to avoid Flask's 4KB cookie limit.
"""

from flask import Blueprint, render_template, request, redirect, session, jsonify
from flask_socketio import join_room, leave_room

import price_feed
from dhan_broker import dhan, dhan_context
from dhanhq import MarketFeed
from indicators import (
    build_oi_df, calculate_max_pain, calculate_pcr, classify_pcr,
    classify_oi_levels, assess_oi_clarity, generate_signals,
)

bp = Blueprint("analyzer", __name__)

INDICES = {
    "NIFTY":      {"security_id": 13,    "lot_size": 65,  "exchange": "NSE_FNO"},
    "BANKNIFTY":  {"security_id": 25,    "lot_size": 30,  "exchange": "NSE_FNO"},
    "FINNIFTY":   {"security_id": 27,    "lot_size": 60,  "exchange": "NSE_FNO"},
    "MIDCPNIFTY": {"security_id": 442,   "lot_size": 120, "exchange": "NSE_FNO"},
    "SENSEX":     {"security_id": 51,    "lot_size": 10,  "exchange": "BSE_FNO"},
}

# ── Server-side chain cache (avoids 4KB cookie limit) ─────────────────────────
_chain: dict = {}          # strike(int) → {"ce": {...}, "pe": {...}}
_sids_map: dict = {}       # str(strike) → {"ce_sid": "...", "pe_sid": "..."}
_exchange: str = "NSE_FNO" # exchange segment for current chain
_symbol: str = "NIFTY"     # instrument name for current chain (NIFTY, BANKNIFTY, …)
_sio = None


def register_socketio(sio):
    global _sio
    _sio = sio

    @sio.on("analyzer_join")
    def on_join(data):
        sid = str(data.get("sid", ""))
        if sid:
            join_room(f"az_{sid}")

    @sio.on("analyzer_leave")
    def on_leave(data):
        sid = str(data.get("sid", ""))
        if sid:
            leave_room(f"az_{sid}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_row(src: dict, side: str) -> dict:
    s = src.get(side) or {}
    greeks = s.get("greeks") or {}
    return {
        "ltp":         float(s.get("last_price") or 0),
        "oi":          int(s.get("oi") or 0),
        "iv":          round(float(s.get("implied_volatility") or 0), 2),
        "delta":       round(float(greeks.get("delta") or 0), 4),
        "theta":       round(float(greeks.get("theta") or 0), 4),
        "gamma":       round(float(greeks.get("gamma") or 0), 6),
        "vega":        round(float(greeks.get("vega") or 0), 4),
        "security_id": str(int(s["security_id"])) if s.get("security_id") else "",
    }


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
        subscribed_sids=_sids_map or None,
        subscribed_strikes=list(_sids_map.keys()),
    )


@bp.route("/analyzer/expiries", methods=["POST"])
def load_expiries():
    instrument = request.json.get("instrument", "NIFTY")
    info       = INDICES.get(instrument)
    if not info:
        return jsonify({"error": "Unknown instrument"}), 400
    try:
        resp = dhan.expiry_list(info["security_id"], dhan.INDEX)
        exps = resp["data"]["data"] if resp.get("status") == "success" else []
        return jsonify({"expiries": exps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/analyzer/chain", methods=["POST"])
def load_chain():
    global _chain, _exchange, _symbol
    instrument = request.json.get("instrument", "NIFTY")
    expiry     = request.json.get("expiry", "")
    info       = INDICES.get(instrument)
    if not info or not expiry:
        return jsonify({"error": "Missing params"}), 400

    try:
        raw = dhan.option_chain(info["security_id"], dhan.INDEX, expiry)
        if raw.get("status") != "success":
            return jsonify({"error": f"API error: {raw.get('remarks', raw)}"}), 500
        inner = raw["data"]["data"]
        oc    = inner["oc"]
        ultp  = float(inner.get("last_price") or 0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    rows = []
    new_chain = {}
    for strike_str, data in oc.items():
        strike = int(float(strike_str))
        ce     = _extract_row(data, "ce")
        pe     = _extract_row(data, "pe")
        rows.append({"strike": strike, "ce": ce, "pe": pe})
        new_chain[strike] = {"ce": ce, "pe": pe}

    rows.sort(key=lambda r: r["strike"])
    _chain    = new_chain
    _exchange = info["exchange"]
    _symbol   = instrument

    # ── OI Intelligence overlay ────────────────────────────────────────────────
    try:
        df        = build_oi_df(new_chain)
        max_pain  = calculate_max_pain(df)
        pcr       = calculate_pcr(df)
        pcr_bias  = classify_pcr(pcr)
        levels    = classify_oi_levels(df)
        clarity   = assess_oi_clarity(levels)
        signals   = generate_signals(df, ultp, max_pain, pcr, pcr_bias, levels, clarity)

        level_map = {l["strike"]: l for l in levels}
        for row in rows:
            lvl = level_map.get(row["strike"])
            row["wall"] = lvl["classification"] if lvl else None
            row["tier"] = lvl["tier"]           if lvl else None
    except Exception:
        max_pain = 0; pcr = 0.0; pcr_bias = "NEUTRAL"
        clarity = "NO_MAP"; signals = []

    return jsonify({
        "rows":            rows,
        "ultp":            ultp,
        "ul_security_id":  str(info["security_id"]),
        "lot_size":        info["lot_size"],
        "max_pain": max_pain,
        "pcr":      pcr,
        "pcr_bias": pcr_bias,
        "clarity":  clarity,
        "signals":  signals,
    })


@bp.route("/analyzer/subscribe", methods=["POST"])
def subscribe():
    global _sids_map, _exchange
    selected_strikes = request.json.get("strikes", [])

    if not selected_strikes:
        return jsonify({"error": "No strikes selected"}), 400
    if not _chain:
        return jsonify({"error": "Load a chain first, then subscribe"}), 400

    new_sids_map     = {}
    feed_instruments = []

    for strike in selected_strikes:
        rec    = _chain.get(int(strike), {})
        ce_sid = (rec.get("ce") or {}).get("security_id", "")
        pe_sid = (rec.get("pe") or {}).get("security_id", "")
        new_sids_map[str(strike)] = {"ce_sid": ce_sid, "pe_sid": pe_sid}
        exch = MarketFeed.BSE_FNO if _exchange == "BSE_FNO" else MarketFeed.NSE_FNO
        if ce_sid:
            feed_instruments.append((exch, ce_sid, MarketFeed.Full))
        if pe_sid:
            feed_instruments.append((exch, pe_sid, MarketFeed.Full))

    if not feed_instruments:
        return jsonify({"error": "No security IDs found for selected strikes"}), 400

    def on_tick(sid, tick):
        ltp = float(tick.get("LTP") or tick.get("last_price") or 0)
        oi  = int(tick.get("OI") or tick.get("oi") or 0)
        if _sio:
            _sio.emit("az_tick", {"sid": sid, "ltp": ltp, "oi": oi},
                      room=f"az_{sid}")

    import feed_manager
    feed_manager.subscribe("analyzer", feed_instruments, on_tick=on_tick)
    _sids_map = new_sids_map

    return jsonify({"ok": True, "sids_map": new_sids_map})


@bp.route("/analyzer/stop", methods=["POST"])
def stop_feed_route():
    global _sids_map
    import feed_manager
    feed_manager.unsubscribe("analyzer")
    _sids_map = {}
    return redirect("/analyzer")
