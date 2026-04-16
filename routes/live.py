"""
Live trade route + SocketIO auto-execution engine.

State machine (server-side _trade dict):
  idle → watching → ordering → active → exiting → idle
"""

import os
import json
from math import floor
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, session, flash

import price_feed
import feed_manager
from dhan_broker import dhan, dhan_context
from dhanhq import MarketFeed

bp = Blueprint("live", __name__)

TRADES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trades")
os.makedirs(TRADES_DIR, exist_ok=True)

# ── Server-side trade state (single user local app) ───────────────────────────
_trade: dict = {"state": "idle"}
_sio = None   # set by register_socketio()


def register_socketio(sio):
    global _sio
    _sio = sio

    @sio.on("live_join")
    def on_live_join(data):
        pass  # browser signals it's ready; nothing to do server-side


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trade_snapshot() -> dict:
    return {k: v for k, v in _trade.items()}


def _save_trade(exit_ltp: float, exit_oid, reason: str):
    os.makedirs(TRADES_DIR, exist_ok=True)
    buy_price = _trade.get("buy_price") or _trade.get("entry", 0)
    pnl       = round((exit_ltp - buy_price) * _trade.get("quantity", 0), 2)
    record    = {
        "exit_reason":    reason,
        "entry_time":     _trade.get("order_time"),
        "exit_time":      datetime.now().isoformat(),
        "trading_symbol": _trade.get("trading_symbol"),
        "security_id":    _trade.get("security_id"),
        "entry_trigger":  _trade.get("entry"),
        "buy_price":      buy_price,
        "exit_price":     exit_ltp,
        "quantity":       _trade.get("quantity"),
        "lots":           _trade.get("lots"),
        "sl":             _trade.get("sl"),
        "targets":        _trade.get("targets"),
        "pnl":            pnl,
        "order_id":       _trade.get("order_id"),
        "exit_order_id":  exit_oid,
    }
    fname = f"trade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(os.path.join(TRADES_DIR, fname), "w") as f:
        json.dump(record, f, indent=2)
    return pnl


def _extract_error(resp: dict) -> str:
    """Pull a readable error string from a Dhan API response."""
    remarks = resp.get("remarks")
    if isinstance(remarks, dict):
        return remarks.get("error_message") or remarks.get("errorMessage") or str(remarks)
    if remarks:
        return str(remarks)
    data = resp.get("data")
    if isinstance(data, dict):
        return data.get("error_message") or data.get("message") or str(data)
    return "Order rejected by broker"


def _do_buy(ltp: float):
    _trade["state"] = "ordering"
    if _sio:
        _sio.emit("trade_update", {"state": "ordering", "ltp": ltp})

    try:
        resp = dhan.place_order(
            security_id=_trade["security_id"],
            exchange_segment=dhan.NSE_FNO,
            transaction_type=dhan.BUY,
            quantity=_trade["quantity"],
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            price=0,
        )
    except Exception as exc:
        _trade["state"] = "watching"
        if _sio:
            _sio.emit("trade_update", {"state": "watching", "error": f"Order error: {exc}"})
        return

    if resp.get("status") == "success":
        _trade.update(
            state=      "active",
            order_id=   resp["data"].get("orderId"),
            buy_price=  ltp,
            order_time= datetime.now().isoformat(),
        )
        if _sio:
            _sio.emit("trade_update", {
                "state":    "active",
                "buy_price": ltp,
                "order_id": _trade["order_id"],
            })
    else:
        _trade["state"] = "watching"
        if _sio:
            _sio.emit("trade_update", {
                "state": "watching",
                "error": f"Order failed: {_extract_error(resp)}",
            })


def _do_exit(ltp: float, reason: str):
    _trade["state"] = "exiting"
    if _sio:
        _sio.emit("trade_update", {"state": "exiting", "ltp": ltp, "reason": reason})

    try:
        resp = dhan.place_order(
            security_id=_trade["security_id"],
            exchange_segment=dhan.NSE_FNO,
            transaction_type=dhan.SELL,
            quantity=_trade["quantity"],
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            price=0,
        )
    except Exception as exc:
        _trade["state"] = "active"
        if _sio:
            _sio.emit("trade_update", {
                "state": "active",
                "error": f"Exit order error: {exc}. Exit manually.",
            })
        return

    order_ok = resp.get("status") == "success"
    exit_oid = resp.get("data", {}).get("orderId") if order_ok else None

    if not order_ok:
        # Order failed — stay active so the user can retry manually
        _trade["state"] = "active"
        if _sio:
            _sio.emit("trade_update", {
                "state": "active",
                "error": f"Exit order FAILED: {_extract_error(resp)}. Exit manually.",
            })
        return

    pnl = _save_trade(ltp, exit_oid, reason)

    if _sio:
        _sio.emit("trade_done", {
            "reason":    reason,
            "ltp":       ltp,
            "buy_price": _trade.get("buy_price"),
            "pnl":       pnl,
        })

    _trade.update(state="idle", buy_price=None, order_id=None, exit_order_id=exit_oid)
    feed_manager.unsubscribe("activetrade_watch")


def _do_partial_exit(ltp: float, qty: int):
    """Sell a partial quantity at market. Updates _trade["quantity"] on success."""
    try:
        resp = dhan.place_order(
            security_id=_trade["security_id"],
            exchange_segment=dhan.NSE_FNO,
            transaction_type=dhan.SELL,
            quantity=qty,
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            price=0,
        )
    except Exception as exc:
        if _sio:
            _sio.emit("trade_update", {"state": "active", "error": f"Partial exit error: {exc}"})
        return False

    if resp.get("status") == "success":
        remaining          = _trade["quantity"] - qty
        _trade["quantity"] = remaining
        _trade["lots"]     = remaining // _trade.get("lot_size", 1)
        if _sio:
            _sio.emit("trade_update", {
                "state":              "active",
                "quantity":           remaining,
                "t1_hit":             True,
                "partial_exit_price": ltp,
            })
        return True
    else:
        if _sio:
            _sio.emit("trade_update", {
                "state": "active",
                "error": f"Partial exit FAILED: {_extract_error(resp)}",
            })
        return False


def _check_auto_trade(sid: str, ltp: float):
    """Called from on_tick callback (background thread). Must be thread-safe."""
    # Ignore non-price ticks (OI updates, status packets return ltp=0)
    if ltp <= 0:
        return
    if str(_trade.get("security_id")) != sid:
        return

    state = _trade.get("state")

    if state == "watching":
        entry   = float(_trade.get("entry") or 0)
        targets = _trade.get("targets", [])
        target1 = float(targets[0]) if targets else None
        if entry and target1:
            buy_mid = (entry + target1) / 2
            if entry < ltp < buy_mid:
                _do_buy(ltp)

    elif state == "active":
        sl        = float(_trade.get("sl") or 0)
        targets   = _trade.get("targets", [])
        target1   = float(targets[0]) if targets else None
        target2   = float(targets[1]) if len(targets) > 1 else None
        buy_price = float(_trade.get("buy_price") or 0)

        # Trailing SL: when price reaches halfway to T1, move SL to quarter mark
        # (only fires once, and only before T1 is hit)
        if target1 and buy_price and not _trade.get("sl_trailed") and not _trade.get("t1_hit"):
            bar       = target1 - buy_price
            half_mark = buy_price + bar / 2
            trail_sl  = buy_price + bar / 4
            if ltp >= half_mark:
                _trade["sl"]         = trail_sl
                _trade["sl_trailed"] = True
                sl                   = trail_sl
                if _sio:
                    _sio.emit("trade_update", {
                        "state":      "active",
                        "sl":         round(trail_sl, 2),
                        "sl_trailed": True,
                    })

        # Guard: set state immediately to prevent duplicate exits from rapid ticks
        if sl and ltp <= sl:
            _trade["state"] = "exiting_guard"
            _do_exit(ltp, "SL")
        elif target2 and _trade.get("t1_hit") and ltp >= target2:
            # Full exit of remaining half at T2
            _trade["state"] = "exiting_guard"
            _do_exit(ltp, "TARGET2")
        elif target1 and not _trade.get("t1_hit") and ltp >= target1:
            if target2:
                # Partial exit: sell half the lots, move SL to midpoint of bar
                qty_exit  = _trade["quantity"] // 2
                bar       = target1 - buy_price
                half_mark = buy_price + bar / 2
                _trade["t1_hit"]     = True
                _trade["sl"]         = half_mark
                _trade["sl_trailed"] = True   # block trailing SL from overriding
                sl                   = half_mark
                _do_partial_exit(ltp, qty_exit)
                if _sio:
                    _sio.emit("trade_update", {
                        "state": "active",
                        "sl":    round(half_mark, 2),
                        "t1_hit": True,
                    })
            else:
                _trade["state"] = "exiting_guard"
                _do_exit(ltp, "TARGET")


def _exch_segment(name: str) -> int:
    return MarketFeed.BSE_FNO if name == "BSE_FNO" else MarketFeed.NSE_FNO

def _feed_mode(exchange_segment: str):
    return MarketFeed.Full if exchange_segment == "BSE_FNO" else MarketFeed.Quote


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/live")
def live_page():
    if "watching" not in session:
        return redirect("/")

    params = session["watching"]

    # Only re-initialise if we're idle (not already in a trade after a refresh)
    if _trade.get("state") == "idle" or str(_trade.get("security_id")) != str(params.get("security_id")):
        # Calculate quantity from funds
        print(f"[live] params from session: {params}")
        lot_size      = int(params.get("lot_size", 65))
        lots_override = params.get("lots_override")
        entry_price   = float(params.get("entry") or 0)

        MAX_LOTS = 20

        if lots_override:
            lots = min(int(lots_override), MAX_LOTS)
        else:
            try:
                resp  = dhan.get_fund_limits()
                funds = float(resp["data"]["availabelBalance"]) if resp.get("status") == "success" else 0
                lots  = floor((funds / 2) / (entry_price * lot_size)) if entry_price > 0 else 0
            except Exception:
                funds = 0
                lots  = 0
            lots = min(lots, MAX_LOTS)

        _trade.update(
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

        # Start price feed with auto-execution callback
        def on_tick(sid, tick):
            ltp = float(tick.get("LTP") or 0)
            if _sio:
                _sio.emit("tick", {
                    "sid":  sid,
                    "ltp":  ltp,
                    "ltt":  tick.get("LTT", ""),
                })
            if ltp > 0:
                _check_auto_trade(sid, ltp)

        print(f"[live] starting feed for security_id={params['security_id']}, lots={lots}, qty={lots * lot_size}")
        feed_manager.subscribe(
            "activetrade_watch",
            [(_exch_segment(params.get("exchange_segment", "NSE_FNO")), str(params["security_id"]), _feed_mode(params.get("exchange_segment", "NSE_FNO")))],
            on_tick=on_tick,
        )

    return render_template("live.html", trade=_trade)


@bp.route("/live/status")
def live_status():
    from flask import jsonify
    return jsonify({
        "trade_state":   _trade.get("state"),
        "security_id":   _trade.get("security_id"),
        "feed_status":   price_feed.feed_status(),
        "feed_cache_keys": list(price_feed.price_cache.keys()),
        "last_error":    price_feed.last_error(),
    })


@bp.route("/live/exit", methods=["POST"])
def exit_trade():
    """Manual exit triggered by button click."""
    if _trade.get("state") == "active":
        ltp_val = request.form.get("ltp", "0")
        try:
            ltp = float(ltp_val)
        except ValueError:
            ltp = float(_trade.get("buy_price") or 0)
        _do_exit(ltp, "MANUAL")
    return redirect("/")


@bp.route("/live/back", methods=["POST"])
def live_back():
    """Cancel watching and go home."""
    feed_manager.unsubscribe("activetrade_watch")
    _trade.update(state="idle")
    session.pop("watching", None)
    return redirect("/")
