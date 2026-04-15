import os
import json
from datetime import date
from flask import Blueprint, render_template, jsonify, request as _req
from dhan_broker import dhan

bp = Blueprint("home", __name__)

TRADES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trades"
)

# Candle-service security IDs for index spot prices
_INDEX_SIDS = {
    "NIFTY":      "13",
    "BANKNIFTY":  "25",
    "FINNIFTY":   "27",
    "MIDCPNIFTY": "442",
}


def _trade_summary():
    """Load trade history and compute P&L summary + today's P&L."""
    trades = []
    today = date.today().isoformat()
    today_pnl = 0.0

    if os.path.isdir(TRADES_DIR):
        for fname in sorted(os.listdir(TRADES_DIR), reverse=True):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(TRADES_DIR, fname)) as f:
                        t = json.load(f)
                    trades.append(t)
                    if t.get("exit_time", "").startswith(today):
                        today_pnl += t.get("pnl", 0)
                except Exception:
                    pass

    total_pnl  = round(sum(t.get("pnl", 0) for t in trades), 2)
    wins       = sum(1 for t in trades if t.get("pnl", 0) > 0)
    losses     = len(trades) - wins
    win_rate   = round(wins / len(trades) * 100, 1) if trades else 0.0
    # Last 10 P&L values (newest first) for sparklines
    pnl_series = [round(t.get("pnl", 0), 2) for t in trades[:10]]

    return {
        "total":      len(trades),
        "pnl":        total_pnl,
        "today_pnl":  round(today_pnl, 2),
        "wins":       wins,
        "losses":     losses,
        "win_rate":   win_rate,
        "pnl_series": pnl_series,
    }, trades[:5]


@bp.route("/")
def index():
    summary, recent_trades = _trade_summary()

    # Open positions from Dhan (for initial server render)
    open_positions = []
    dhan_status = {"ok": False, "count": 0}
    try:
        pos = dhan.get_positions()
        if pos.get("status") == "success":
            open_positions = pos.get("data", [])
            dhan_status = {"ok": True, "count": len(open_positions)}
    except Exception:
        pass

    return render_template(
        "home.html",
        dhan=dhan_status,
        summary=summary,
        open_positions=open_positions,
        recent_trades=recent_trades,
    )


@bp.route("/api/home/snapshot")
def home_snapshot():
    """
    Polled every 5 s by home.html JS.
    Returns index prices, active trade state, and today's P&L summary.
    """
    from price_feed import price_cache
    import routes.live as _live

    # ── Index spot prices ─────────────────────────────────────────────────
    # Priority: 1) live WebSocket tick  2) live candle close  3) last candle
    import candle_service
    indices = {}
    for name, sid in _INDEX_SIDS.items():
        ltp = None
        # 1. Real-time tick (only available when a feed is running)
        tick = price_cache.get(sid, {})
        raw  = tick.get("LTP") or tick.get("ltp")
        if raw:
            ltp = round(float(raw), 2)
        # 2. Current in-progress 5-min bar close
        if ltp is None:
            try:
                live = candle_service.get_live_candle(name)
                if live and live.get("close"):
                    ltp = round(float(live["close"]), 2)
            except Exception:
                pass
        # 3. Latest completed candle close
        if ltp is None:
            try:
                rows = candle_service.get_candles(name, n=1)
                if rows and rows[0].get("close"):
                    ltp = round(float(rows[0]["close"]), 2)
            except Exception:
                pass
        indices[name] = ltp

    # ── Active trade from live.py module state ────────────────────────────
    trade_state = _live._trade.get("state", "idle")
    active_trade = None
    if trade_state != "idle":
        t = _live._trade
        active_trade = {
            "symbol":  t.get("trading_symbol"),
            "state":   trade_state,
            "entry":   t.get("entry"),
            "sl":      t.get("sl"),
            "targets": t.get("targets", []),
            "lots":    t.get("lots"),
        }

    # ── Today's P&L ───────────────────────────────────────────────────────
    summary, _ = _trade_summary()

    # ── Open positions count ──────────────────────────────────────────────
    open_count = 0
    try:
        pos = dhan.get_positions()
        if pos.get("status") == "success":
            open_count = len(pos.get("data", []))
    except Exception:
        pass

    return jsonify({
        "indices":      indices,
        "active_trade": active_trade,
        "summary":      summary,
        "open_count":   open_count,
    })


@bp.route("/api/home/chart")
def home_chart():
    """
    Intraday 5-min candle closes for an index — used by the dashboard main chart.
    Returns up to 78 bars (6.5 h × 12 bars/h) oldest-first.
    """
    import candle_service
    name = _req.args.get("index", "NIFTY").upper()
    if name not in _INDEX_SIDS:
        return jsonify({"labels": [], "closes": []})
    rows = candle_service.get_candles(name, n=78)       # already oldest → newest
    labels = [r["time"][11:16] for r in rows]            # "HH:MM"
    closes = [round(float(r["close"]), 2) for r in rows]
    return jsonify({"labels": labels, "closes": closes})
