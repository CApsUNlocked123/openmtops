"""
OI Tracker — records Open Interest changes for selected strikes over time.

State: idle → tracking → idle
"""

import logging
from datetime import datetime
from math import floor
from flask import Blueprint, render_template, redirect, request, jsonify, session, flash
from flask_socketio import join_room

import price_feed
from dhan_broker import dhan, dhan_context, lookup_security
from dhanhq import MarketFeed

log = logging.getLogger(__name__)
bp = Blueprint("oi_tracker", __name__)

# ── Server-side tracker state ─────────────────────────────────────────────────
_tracker: dict = {"state": "idle"}
_sio = None

# Flag a tick as "large" if single-trade quantity >= this many lots
LARGE_ORDER_LOTS = 5


def register_socketio(sio):
    global _sio
    _sio = sio

    @sio.on("oi_tracker_join")
    def on_join(data):
        join_room("oi_tracker")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _buildup_pattern(ltp_now: float, ltp_base: float, oi_delta: int) -> str:
    """Classify OI buildup pattern from price and OI change direction."""
    price_up = ltp_now > ltp_base
    oi_up    = oi_delta > 0
    if price_up  and oi_up:   return "Long Buildup"
    if not price_up and oi_up: return "Short Buildup"
    if not price_up and not oi_up: return "Long Unwinding"
    if price_up  and not oi_up: return "Short Covering"
    return "—"


# ── KPI builder ───────────────────────────────────────────────────────────────

def _compute_kpis() -> dict:
    baseline = _tracker.get("baseline", {})
    current  = _tracker.get("current",  {})
    sids_map = _tracker.get("sids_map", {})

    rows = []
    total_ce_base = total_pe_base = 0
    total_ce_now  = total_pe_now  = 0

    for strike in sorted(sids_map, key=lambda x: int(x)):
        sids   = sids_map[strike]
        ce_sid = sids.get("ce_sid", "")
        pe_sid = sids.get("pe_sid", "")

        ce_base = baseline.get(ce_sid, {}).get("oi", 0)
        pe_base = baseline.get(pe_sid, {}).get("oi", 0)
        ce_now  = current.get(ce_sid,  {}).get("oi", ce_base)
        pe_now  = current.get(pe_sid,  {}).get("oi", pe_base)
        ce_ltp  = current.get(ce_sid,  {}).get("ltp", 0)
        pe_ltp  = current.get(pe_sid,  {}).get("ltp", 0)

        ce_delta = ce_now - ce_base
        pe_delta = pe_now - pe_base
        ce_pct   = round(ce_delta / ce_base * 100, 2) if ce_base else 0.0
        pe_pct   = round(pe_delta / pe_base * 100, 2) if pe_base else 0.0

        ce_ltp_base = baseline.get(ce_sid, {}).get("ltp", 0)
        pe_ltp_base = baseline.get(pe_sid, {}).get("ltp", 0)

        # Buildup pattern per side
        ce_pattern = _buildup_pattern(ce_ltp, ce_ltp_base, ce_delta)
        pe_pattern = _buildup_pattern(pe_ltp, pe_ltp_base, pe_delta)

        rows.append({
            "strike":      int(strike),
            "ce_oi":       ce_now,
            "ce_oi_base":  ce_base,
            "ce_delta":    ce_delta,
            "ce_pct":      ce_pct,
            "ce_ltp":      ce_ltp,
            "ce_pattern":  ce_pattern,
            "pe_oi":       pe_now,
            "pe_oi_base":  pe_base,
            "pe_delta":    pe_delta,
            "pe_pct":      pe_pct,
            "pe_ltp":      pe_ltp,
            "pe_pattern":  pe_pattern,
        })

        total_ce_base += ce_base
        total_pe_base += pe_base
        total_ce_now  += ce_now
        total_pe_now  += pe_now

    pcr_base = round(total_pe_base / total_ce_base, 3) if total_ce_base else 0.0
    pcr_now  = round(total_pe_now  / total_ce_now,  3) if total_ce_now  else 0.0
    pcr_chg  = round(pcr_now - pcr_base, 3)

    # IV Skew (stored at baseline, doesn't change unless restated)
    iv_data    = _tracker.get("iv_baseline", {})
    ce_ivs     = [v["ce"] for v in iv_data.values() if v.get("ce", 0) > 0]
    pe_ivs     = [v["pe"] for v in iv_data.values() if v.get("pe", 0) > 0]
    avg_ce_iv  = round(sum(ce_ivs) / len(ce_ivs), 2) if ce_ivs else 0.0
    avg_pe_iv  = round(sum(pe_ivs) / len(pe_ivs), 2) if pe_ivs else 0.0
    iv_skew_ratio = round(avg_pe_iv / avg_ce_iv, 3) if avg_ce_iv else 0.0
    iv_skew_label = (
        "PUT SKEW — fear of downside" if iv_skew_ratio > 1.05 else
        "CALL SKEW — chasing upside"  if iv_skew_ratio < 0.95 else
        "BALANCED"
    )

    # Straddle cost + live spot derived from ATM put-call parity
    # Parity: Spot ≈ ATM_Strike + CE_LTP − PE_LTP  (works for European-style index options)
    atm_strike    = _tracker.get("atm_strike")
    straddle_cost = 0.0
    straddle_pct  = 0.0
    live_ultp     = _tracker.get("ultp", 0)   # fallback = value at tracking start
    if atm_strike:
        atm_sids   = _tracker.get("sids_map", {}).get(str(atm_strike), {})
        atm_ce_ltp = current.get(atm_sids.get("ce_sid", ""), {}).get("ltp", 0)
        atm_pe_ltp = current.get(atm_sids.get("pe_sid", ""), {}).get("ltp", 0)
        straddle_cost = round(atm_ce_ltp + atm_pe_ltp, 2)
        if atm_ce_ltp > 0 and atm_pe_ltp > 0:
            live_ultp = round(atm_strike + atm_ce_ltp - atm_pe_ltp, 2)
        if live_ultp and straddle_cost:
            straddle_pct = round(straddle_cost / live_ultp * 100, 2)

    duration = ""
    start_iso = _tracker.get("start_time")
    if start_iso:
        secs     = int((datetime.now() - datetime.fromisoformat(start_iso)).total_seconds())
        duration = f"{secs // 60}m {secs % 60}s"

    return {
        "state":          _tracker.get("state", "idle"),
        "start_time":     start_iso,
        "start_display":  start_iso[:19].replace("T", " ") if start_iso else "—",
        "duration":       duration,
        "pcr_base":       pcr_base,
        "pcr_now":        pcr_now,
        "pcr_change":     pcr_chg,
        "total_ce_delta": total_ce_now  - total_ce_base,
        "total_pe_delta": total_pe_now  - total_pe_base,
        "rows":           rows,
        "large_orders":   list(reversed(_tracker.get("large_orders", [])[-100:])),
        "avg_ce_iv":      avg_ce_iv,
        "avg_pe_iv":      avg_pe_iv,
        "iv_skew_ratio":  iv_skew_ratio,
        "iv_skew_label":  iv_skew_label,
        "straddle_cost":  straddle_cost,
        "straddle_pct":   straddle_pct,
        "atm_strike":     atm_strike,
        "ultp":           live_ultp,
    }


# ── Tick callback (background thread) ─────────────────────────────────────────

def _on_tick(sid: str, tick: dict):
    ltp = float(tick.get("LTP") or tick.get("last_price") or 0)
    oi  = int(tick.get("OI")  or tick.get("oi")           or 0)
    ltq = int(tick.get("LTQ") or tick.get("last_traded_quantity") or 0)

    # Underlying index tick — update spot price and emit KPI so UI refreshes immediately
    if sid == _tracker.get("ul_sid"):
        if ltp > 0:
            _tracker["ultp"] = ltp
            if _sio:
                _sio.emit("oi_update", _compute_kpis(), room="oi_tracker")
        return

    cur = _tracker.get("current", {})
    if sid not in cur:
        return

    if ltp > 0:
        cur[sid]["ltp"] = ltp
    if oi > 0:
        cur[sid]["oi"] = oi

    # Large order detection
    lot_size  = _tracker.get("lot_size", 1)
    threshold = LARGE_ORDER_LOTS * lot_size
    if ltq >= threshold:
        for strike, sids in _tracker.get("sids_map", {}).items():
            if sids.get("ce_sid") == sid:
                opt_type = "CE"; break
            if sids.get("pe_sid") == sid:
                opt_type = "PE"; strike = strike; break
        else:
            opt_type = "?"
            strike   = "?"
        _tracker["large_orders"].append({
            "time":   datetime.now().strftime("%H:%M:%S"),
            "strike": strike,
            "type":   opt_type,
            "ltp":    ltp,
            "qty":    ltq,
            "oi":     oi,
        })

    if _sio:
        _sio.emit("oi_update", _compute_kpis(), room="oi_tracker")


# ── Dashboard auto-start ──────────────────────────────────────────────────────

def _extract_raw_option(s: dict) -> dict:
    """Extract fields from a raw option chain entry (before _extract_row processing)."""
    return {
        "security_id": str(int(s["security_id"])) if s.get("security_id") else "",
        "oi":          int(s.get("oi") or 0),
        "ltp":         float(s.get("last_price") or 0),
        "iv":          float(s.get("implied_volatility") or 0),
    }


def start_for_instrument(instrument: str) -> dict:
    """
    Auto-start OI tracking for a given instrument from the dashboard.
    Fetches the nearest weekly expiry, selects ATM±5 strikes, starts feed.
    Skips silently if already tracking (doesn't disrupt existing session).
    Returns {"ok": True} or {"error": "reason"}.
    """
    global _tracker

    if _tracker.get("state") == "tracking":
        return {"ok": True, "already_tracking": True}

    try:
        from routes.analyzer import INDICES
        info = INDICES.get(instrument.upper())
        if not info:
            return {"error": f"Unknown instrument: {instrument}"}

        security_id = info["security_id"]
        lot_size    = info["lot_size"]
        exchange    = info["exchange"]

        # Fetch nearest expiry
        resp_exp = dhan.expiry_list(security_id, dhan.INDEX)
        if resp_exp.get("status") != "success":
            return {"error": "Failed to fetch expiry list"}
        expiries = resp_exp["data"]["data"]
        if not expiries:
            return {"error": "No expiries available"}
        nearest_expiry = expiries[0]

        # Load option chain
        raw = dhan.option_chain(security_id, dhan.INDEX, nearest_expiry)
        if raw.get("status") != "success":
            return {"error": f"Failed to load chain: {raw.get('remarks', '')}"}

        inner = raw["data"]["data"]
        oc    = inner["oc"]
        ultp  = float(inner.get("last_price") or 0)

        if not ultp:
            return {"error": "Could not determine spot price from chain"}

        # Normalize strike keys to int
        oc_norm     = {int(float(k)): v for k, v in oc.items()}
        all_strikes = sorted(oc_norm.keys())

        # If option chain returned ultp=0 (market offline / pre-open),
        # fall back to the last stored candle price, then to mid-strike.
        if not ultp:
            try:
                from candle_service import get_candles
                last = get_candles(instrument, n=1)
                if last:
                    ultp = float(last[-1].get("close") or 0)
            except Exception:
                pass
        if not ultp and all_strikes:
            ultp = float(all_strikes[len(all_strikes) // 2])
        if not ultp:
            return {"error": "Could not determine spot price from chain"}

        atm_strike = min(all_strikes, key=lambda s: abs(s - ultp))
        atm_idx    = all_strikes.index(atm_strike)

        lo = max(0, atm_idx - 5)
        hi = min(len(all_strikes) - 1, atm_idx + 5)
        selected_strikes = all_strikes[lo : hi + 1]

        exch_mf          = MarketFeed.BSE_FNO if exchange == "BSE_FNO" else MarketFeed.NSE_FNO
        baseline         = {}
        current          = {}
        sids_map         = {}
        iv_baseline      = {}
        feed_instruments = []

        for strike in selected_strikes:
            rec    = oc_norm.get(strike, {})
            ce     = _extract_raw_option(rec.get("ce") or {})
            pe     = _extract_raw_option(rec.get("pe") or {})
            ce_sid = ce["security_id"]
            pe_sid = pe["security_id"]

            sids_map[str(strike)]    = {"ce_sid": ce_sid, "pe_sid": pe_sid}
            iv_baseline[str(strike)] = {"ce": ce["iv"], "pe": pe["iv"]}

            if ce_sid:
                baseline[ce_sid] = {"oi": ce["oi"], "ltp": ce["ltp"]}
                current[ce_sid]  = dict(baseline[ce_sid])
                feed_instruments.append((exch_mf, ce_sid, MarketFeed.Full))
            if pe_sid:
                baseline[pe_sid] = {"oi": pe["oi"], "ltp": pe["ltp"]}
                current[pe_sid]  = dict(baseline[pe_sid])
                feed_instruments.append((exch_mf, pe_sid, MarketFeed.Full))

        if not feed_instruments:
            return {"error": "No security IDs found for selected strikes"}

        ul_sid = str(security_id)
        feed_instruments.append((exch_mf, ul_sid, MarketFeed.Full))

        _tracker = {
            "state":        "tracking",
            "start_time":   datetime.now().isoformat(),
            "sids_map":     sids_map,
            "baseline":     baseline,
            "current":      current,
            "large_orders": [],
            "lot_size":     lot_size,
            "ultp":         ultp,
            "ul_sid":       ul_sid,
            "atm_strike":   atm_strike,
            "iv_baseline":  iv_baseline,
        }

        import feed_manager
        feed_manager.subscribe("oi_tracker", feed_instruments, on_tick=_on_tick)
        log.info("[oi_tracker] dashboard auto-start: %s ATM=%s strikes=%s",
                 instrument, atm_strike, selected_strikes)
        return {"ok": True, "atm_strike": atm_strike, "strikes": selected_strikes}

    except Exception as e:
        log.error("[oi_tracker] start_for_instrument error: %s", e)
        return {"error": str(e)}


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/oi_tracker")
def oi_tracker_page():
    if _tracker.get("state") == "idle":
        return redirect("/analyzer")
    return render_template("oi_tracker.html", kpis=_compute_kpis())


@bp.route("/oi_tracker/start", methods=["POST"])
def start_tracking():
    global _tracker

    # Import live chain data from analyzer (already loaded server-side)
    from routes.analyzer import _chain, _exchange

    selected_strikes = request.json.get("strikes", [])
    lot_size         = int(request.json.get("lot_size", 65))
    ultp             = float(request.json.get("ultp", 0))
    ul_security_id   = str(request.json.get("ul_security_id", "") or "")

    if not selected_strikes:
        return jsonify({"error": "No strikes selected"}), 400
    if not _chain:
        return jsonify({"error": "Load an option chain first"}), 400

    baseline         = {}
    current          = {}
    sids_map         = {}
    iv_baseline      = {}   # strike → {ce: iv, pe: iv}
    feed_instruments = []
    exch = MarketFeed.BSE_FNO if _exchange == "BSE_FNO" else MarketFeed.NSE_FNO

    # Find ATM strike (closest to underlying LTP)
    strikes_int = [int(s) for s in selected_strikes]
    atm_strike  = min(strikes_int, key=lambda s: abs(s - ultp)) if ultp and strikes_int else None

    for strike in selected_strikes:
        rec    = _chain.get(int(strike), {})
        ce     = rec.get("ce") or {}
        pe     = rec.get("pe") or {}
        ce_sid = ce.get("security_id", "")
        pe_sid = pe.get("security_id", "")

        sids_map[str(strike)] = {"ce_sid": ce_sid, "pe_sid": pe_sid}
        iv_baseline[str(strike)] = {
            "ce": float(ce.get("iv") or 0),
            "pe": float(pe.get("iv") or 0),
        }

        if ce_sid:
            baseline[ce_sid] = {"oi": int(ce.get("oi") or 0), "ltp": float(ce.get("ltp") or 0)}
            current[ce_sid]  = dict(baseline[ce_sid])
            feed_instruments.append((exch, ce_sid, MarketFeed.Full))
        if pe_sid:
            baseline[pe_sid] = {"oi": int(pe.get("oi") or 0), "ltp": float(pe.get("ltp") or 0)}
            current[pe_sid]  = dict(baseline[pe_sid])
            feed_instruments.append((exch, pe_sid, MarketFeed.Full))

    if not feed_instruments:
        return jsonify({"error": "No security IDs found for selected strikes"}), 400

    # Subscribe to the underlying index for live spot price updates
    if ul_security_id:
        feed_instruments.append((exch, ul_security_id, MarketFeed.Full))

    _tracker = {
        "state":        "tracking",
        "start_time":   datetime.now().isoformat(),
        "sids_map":     sids_map,
        "baseline":     baseline,
        "current":      current,
        "large_orders": [],
        "lot_size":     lot_size,
        "ultp":         ultp,
        "ul_sid":       ul_security_id,
        "atm_strike":   atm_strike,
        "iv_baseline":  iv_baseline,
    }

    import feed_manager
    feed_manager.subscribe("oi_tracker", feed_instruments, on_tick=_on_tick)
    return jsonify({"ok": True})


@bp.route("/oi_tracker/kpis")
def get_kpis():
    """Polled endpoint for duration refresh (lightweight)."""
    if _tracker.get("state") == "idle":
        return jsonify({"state": "idle"})
    return jsonify({"duration": _compute_kpis()["duration"]})


@bp.route("/oi_tracker/stop", methods=["POST"])
def stop_tracking():
    global _tracker
    import feed_manager
    feed_manager.unsubscribe("oi_tracker")
    _tracker = {"state": "idle"}
    return redirect("/analyzer")


@bp.route("/oi_tracker/quick_trade", methods=["POST"])
def quick_trade():
    from routes.analyzer import _symbol

    strike      = request.form.get("strike", "").strip()
    option_type = request.form.get("option_type", "CE").upper()
    entry       = request.form.get("entry", "").strip()
    sl          = request.form.get("sl", "").strip()
    targets_raw = request.form.get("targets", "").strip()
    lots_mode   = request.form.get("lots_mode", "auto")
    lots_manual = request.form.get("lots_manual", "").strip()

    if not entry or not targets_raw:
        flash("Entry and at least one target are required.", "warning")
        return redirect("/oi_tracker")

    targets = [t.strip() for t in targets_raw.split(",") if t.strip()]

    sec = lookup_security(_symbol, strike, option_type)
    if not sec:
        flash(f"No contract found for {_symbol} {strike} {option_type}.", "warning")
        return redirect("/oi_tracker")

    lot_size = sec["lot_size"]
    if lots_mode == "manual" and lots_manual:
        lots_override = int(lots_manual)
    else:
        lots_override = None
        try:
            resp  = dhan.get_fund_limits()
            funds = float(resp["data"]["availabelBalance"]) if resp.get("status") == "success" else 0
            lots_override = floor(funds / (float(entry) * lot_size)) if float(entry) > 0 else 0
        except Exception:
            lots_override = 0

    session["watching"] = {
        "security_id":    sec["security_id"],
        "trading_symbol": sec["trading_symbol"],
        "expiry":         sec["expiry"],
        "lot_size":       lot_size,
        "entry":          entry,
        "sl":             sl,
        "targets":        targets,
        "lots_override":  lots_override,
    }
    return redirect("/live")
