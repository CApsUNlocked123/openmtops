"""
Profile — combined Settings + History page with two tabs.
All form POST actions still point to /settings/* (no duplication of logic).
"""

import os
import json
from datetime import date
from flask import Blueprint, render_template, session

from telegram_client import is_authorized
from runtime_config import get_dhan_credentials, get_telegram_channel_id, _load, get as _cfg

bp = Blueprint("profile", __name__)

TRADES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trades"
)


def _load_history():
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
    losses    = len(trades) - wins
    win_rate  = round(wins / len(trades) * 100, 1) if trades else 0.0
    summary   = {"total": len(trades), "pnl": total_pnl,
                  "wins": wins, "losses": losses, "win_rate": win_rate}
    return trades, summary


@bp.route("/profile")
def profile():
    # ── Settings data ──────────────────────────────────────────────────────
    if session.get("tg_step"):
        tg_authorized = False
    else:
        tg_authorized = is_authorized()
        if tg_authorized:
            session["tg_authorized"] = True
        else:
            session.pop("tg_authorized", None)

    client_id, token = get_dhan_credentials()
    masked_token = (token[:8] + "..." + token[-4:]) if len(token) > 12 else "***"

    cfg = _load()
    tg  = cfg.get("telegram", {})

    # ── History data ───────────────────────────────────────────────────────
    trades, summary = _load_history()

    # Active tab: ?tab=history or ?tab=settings (default settings)
    from flask import request
    active_tab = request.args.get("tab", "settings")

    return render_template(
        "profile.html",
        # settings
        tg_authorized=tg_authorized,
        tg_step=session.get("tg_step", "phone"),
        client_id=client_id,
        masked_token=masked_token,
        tg_api_id=tg.get("api_id", ""),
        tg_api_hash=tg.get("api_hash", ""),
        tg_api_configured=bool(tg.get("api_id") and tg.get("api_hash")),
        current_channel_id=get_telegram_channel_id(),
        pin_set=bool(_cfg("app_pin")),
        # history
        trades=trades,
        summary=summary,
        # tab state
        active_tab=active_tab,
    )
