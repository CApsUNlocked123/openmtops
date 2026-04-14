"""
ActiveTrade — 80/20 split screen: Strategy Dashboard (left) + Live Trade Panel (right).

Trade initialisation mirrors routes/live.py GET /live exactly.
routes/live.py is NOT modified — its module-level _trade dict, _check_auto_trade,
_do_exit and SocketIO helpers are reused via direct module import.
"""

from flask import Blueprint, render_template, session, redirect, request, jsonify
from math import floor

import price_feed
from dhan_broker import dhan, dhan_context
from candle_service import INSTRUMENT_NAMES

bp = Blueprint("activetrade", __name__)

_MAX_LOTS = 20


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

    price_feed.start_feed(
        dhan_context,
        [(_live._exch_segment(params.get("exchange_segment", "NSE_FNO")),
          str(params["security_id"]),
          _live._feed_mode(params.get("exchange_segment", "NSE_FNO")))],
        on_tick=on_tick,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/activetrade")
def activetrade_page():
    import routes.live as _live

    if "watching" in session:
        params = session["watching"]

        current_state = _live._trade.get("state", "idle")
        current_sid   = str(_live._trade.get("security_id") or "")
        new_sid       = str(params.get("security_id") or "")

        # Never reinitialise mid-execution — an open position must be exited first.
        position_live = current_state in ("active", "ordering", "exiting", "exiting_guard")

        # Reinitialise when:
        #   a) No security loaded yet (first visit), OR
        #   b) Instrument changed (user set up a different trade), OR
        #   c) State is idle (previous trade finished / cancelled)
        need_init = (
            not position_live
            and (current_sid != new_sid or current_state == "idle")
        )
        if need_init:
            _init_trade_from_session(params)

    trade = dict(_live._trade) if _live._trade.get("state") != "idle" else None
    return render_template("activetrade.html", trade=trade, instruments=INSTRUMENT_NAMES)


@bp.route("/activetrade/exit", methods=["POST"])
def activetrade_exit():
    """Manual exit — mirrors /live/exit but stays on /activetrade afterwards."""
    import routes.live as _live

    if _live._trade.get("state") == "active":
        try:
            ltp = float(request.form.get("ltp", "0"))
        except ValueError:
            ltp = float(_live._trade.get("buy_price") or 0)
        _live._do_exit(ltp, "MANUAL")

    session.pop("watching", None)
    return redirect("/activetrade")


@bp.route("/activetrade/cancel", methods=["POST"])
def activetrade_cancel():
    """Cancel watching / reset trade state without placing any order."""
    import routes.live as _live
    state = _live._trade.get("state")
    # Only cancel safe (non-position) states — refuse if a real order is live.
    if state not in ("active", "ordering", "exiting", "exiting_guard"):
        price_feed.stop_feed()
        _live._trade.update(state="idle")
    session.pop("watching", None)
    return redirect("/activetrade")


@bp.route("/activetrade/clear", methods=["POST"])
def activetrade_clear():
    """Called by JS after auto-exit (trade_done) to prevent re-init on refresh."""
    session.pop("watching", None)
    return jsonify({"ok": True})
