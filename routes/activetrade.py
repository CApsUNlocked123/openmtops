"""
ActiveTrade — 80/20 split screen: Strategy Dashboard (left) + Live Trade Panel (right).

Trade initialisation mirrors routes/live.py GET /live exactly.
routes/live.py is NOT modified — its module-level _trade dict, _check_auto_trade,
_do_exit and SocketIO helpers are reused via direct module import.

Primary URL: /trade   (legacy /activetrade → 301 → /trade)
"""

from flask import Blueprint, render_template, session, redirect, request, jsonify, flash
from math import floor

import price_feed
import feed_manager
from dhan_broker import dhan, dhan_context, lookup_security
from candle_service import INSTRUMENT_NAMES

bp = Blueprint("activetrade", __name__)

_MAX_LOTS = 20
INDICES   = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]


@bp.route("/activetrade")
def activetrade_alias():
    """Legacy URL — permanent redirect to new primary /trade."""
    return redirect("/trade", 301)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _init_trade_from_session(params: dict) -> None:
    """Initialise _trade and start the price feed from session watching params.

    Mirrors the initialisation block in routes/live.py GET /live verbatim so
    the trade state machine (which lives entirely in live.py) stays untouched.
    """
    import routes.live as _live

    lot_size      = int(params.get("lot_size", 65))
    lots_override = params.get("lots_override")
    entry_price   = float(params.get("entry") or 0)

    if lots_override:
        lots = min(int(lots_override), _MAX_LOTS)
    else:
        try:
            resp  = dhan.get_fund_limits()
            funds = float(resp["data"]["availabelBalance"]) if resp.get("status") == "success" else 0
            lots  = floor((funds / 2) / (entry_price * lot_size)) if entry_price > 0 else 0
        except Exception:
            lots = 0
        lots = min(lots, _MAX_LOTS)

    _live._trade.update(
        state          = "watching",
        security_id    = params["security_id"],
        trading_symbol = params["trading_symbol"],
        expiry         = params.get("expiry", ""),
        entry          = entry_price,
        sl             = float(params.get("sl") or 0),
        targets        = [float(t) for t in params.get("targets", []) if t],
        lot_size       = lot_size,
        lots           = lots,
        quantity       = lots * lot_size,
        buy_price      = None,
        order_id       = None,
        sl_trailed     = False,
        t1_hit         = False,
    )

    def on_tick(sid, tick):
        ltp = float(tick.get("LTP") or 0)
        if _live._sio:
            _live._sio.emit("tick", {
                "sid": sid,
                "ltp": ltp,
                "ltt": tick.get("LTT", ""),
            })
        if ltp > 0:
            _live._check_auto_trade(sid, ltp)

    # Register the watch through feed_manager so it coexists with other
    # subscribers (e.g. OI tracker). Using price_feed.start_feed directly
    # would stop any running shared feed and wipe those subscriptions.
    feed_manager.subscribe(
        "activetrade_watch",
        [(_live._exch_segment(params.get("exchange_segment", "NSE_FNO")),
          str(params["security_id"]),
          _live._feed_mode(params.get("exchange_segment", "NSE_FNO")))],
        on_tick=on_tick,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/trade")
def activetrade_page():
    import routes.live as _live

    if "watching" in session:
        params = session["watching"]

        current_state = _live._trade.get("state", "idle")
        current_sid   = str(_live._trade.get("security_id") or "")
        new_sid       = str(params.get("security_id") or "")

        # Never reinitialise mid-execution — an open position must be exited first.
        position_live = current_state in ("active", "ordering", "exiting", "exiting_guard")

        need_init = (
            not position_live
            and (current_sid != new_sid or current_state == "idle")
        )
        if need_init:
            _init_trade_from_session(params)

    trade = dict(_live._trade) if _live._trade.get("state") != "idle" else None
    return render_template("activetrade.html", trade=trade, instruments=INSTRUMENT_NAMES)


@bp.route("/trade/setup", methods=["POST"])
def trade_setup():
    """Inline setup form submission — stores session and returns to /trade."""
    instrument  = request.form.get("instrument", "NIFTY").upper()
    strike      = request.form.get("strike", "").strip()
    option_type = request.form.get("option_type", "CE").upper()
    entry       = request.form.get("entry", "").strip()
    sl          = request.form.get("sl", "").strip()
    targets_raw = request.form.get("targets", "").strip()
    lots_manual = request.form.get("lots_manual", "").strip()

    if not entry or not targets_raw:
        flash("Entry and at least one target are required.", "warning")
        return redirect("/trade")

    targets = [t.strip() for t in targets_raw.split(",") if t.strip()]

    try:
        sec = lookup_security(instrument, strike, option_type)
    except Exception as e:
        flash(f"Instrument lookup failed: {e}", "danger")
        return redirect("/trade")
    if not sec:
        flash(f"No contract found for {instrument} {strike} {option_type}.", "warning")
        return redirect("/trade")

    session["watching"] = {
        "security_id":      sec["security_id"],
        "trading_symbol":   sec["trading_symbol"],
        "expiry":           sec["expiry"],
        "lot_size":         sec["lot_size"],
        "exchange_segment": sec["exchange_segment"],
        "entry":            entry,
        "sl":               sl,
        "targets":          targets,
        "lots_override":    int(lots_manual) if lots_manual else None,
        "mode":             "single",
    }
    return redirect("/trade")


@bp.route("/activetrade/exit", methods=["POST"])
def activetrade_exit():
    """Manual exit — mirrors /live/exit but stays on /trade afterwards."""
    import routes.live as _live

    if _live._trade.get("state") == "active":
        try:
            ltp = float(request.form.get("ltp", "0"))
        except ValueError:
            ltp = float(_live._trade.get("buy_price") or 0)
        _live._do_exit(ltp, "MANUAL")

    session.pop("watching", None)
    return redirect("/trade")


@bp.route("/activetrade/cancel", methods=["POST"])
def activetrade_cancel():
    """Cancel watching / reset trade state without placing any order."""
    import routes.live as _live
    state = _live._trade.get("state")
    if state not in ("active", "ordering", "exiting", "exiting_guard"):
        feed_manager.unsubscribe("activetrade_watch")
        _live._trade.update(state="idle")
    session.pop("watching", None)
    return redirect("/trade")


@bp.route("/activetrade/clear", methods=["POST"])
def activetrade_clear():
    """Called by JS after auto-exit (trade_done) to prevent re-init on refresh."""
    session.pop("watching", None)
    return jsonify({"ok": True})
