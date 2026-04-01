import os
import json
from flask import Blueprint, render_template

bp = Blueprint("history", __name__)

TRADES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trades")


@bp.route("/history")
def history():
    trades = []
    if os.path.isdir(TRADES_DIR):
        for fname in sorted(os.listdir(TRADES_DIR), reverse=True):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(TRADES_DIR, fname)) as f:
                        trades.append(json.load(f))
                except Exception:
                    pass

    total_pnl = round(sum(t.get("pnl", 0) for t in trades), 2)
    wins      = sum(1 for t in trades if t.get("pnl", 0) > 0)
    losses    = sum(1 for t in trades if t.get("pnl", 0) <= 0)
    win_rate  = round(wins / len(trades) * 100, 1) if trades else 0.0

    summary = {
        "total":    len(trades),
        "pnl":      total_pnl,
        "wins":     wins,
        "losses":   losses,
        "win_rate": win_rate,
    }
    return render_template("history.html", trades=trades, summary=summary)
