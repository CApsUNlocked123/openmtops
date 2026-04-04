"""
Auth routes — PIN gate and API credential status page.
"""

import os
import time
import base64
import json
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, render_template, request, session, redirect
from tgwrap import is_authorized

bp = Blueprint("auth", __name__)

# ip → [timestamp]  (rate limit: 5 attempts per hour per IP)
_attempts: dict = defaultdict(list)


# ── helpers ───────────────────────────────────────────────────────────────────

def check_dhan_token() -> dict:
    """Decode JWT exp claim (no signature verification) to assess validity."""
    from runtime_config import get_dhan_credentials
    _, token = get_dhan_credentials()   # prefers runtime_config.json over globals
    try:
        seg = token.split('.')[1]
        seg += '=' * (4 - len(seg) % 4)   # fix base64 padding
        payload = json.loads(base64.b64decode(seg))
        exp   = int(payload.get('exp', 0))
        valid = exp > time.time()
        label = datetime.fromtimestamp(exp).strftime('%d %b %Y %H:%M') if exp else '?'
        return {"valid": valid, "expires_at": label}
    except Exception:
        return {"valid": bool(token), "expires_at": "Unknown"}


def check_tg_session() -> dict:
    """Return current Telegram session health."""
    try:
        ok = is_authorized()
        return {"valid": bool(ok)}
    except Exception:
        return {"valid": False}


def both_valid() -> bool:
    return check_dhan_token()["valid"] and check_tg_session()["valid"]


# ── routes ────────────────────────────────────────────────────────────────────

@bp.route("/pin", methods=["GET", "POST"])
def pin_page():
    APP_PIN = os.getenv("APP_PIN", "")
    if not APP_PIN:
        # No PIN configured — skip gate but still verify API health
        session["pin_ok"] = True
        return redirect("/auth/status")

    ip  = request.remote_addr
    now = time.time()

    if request.method == "POST":
        # Purge old attempts (older than 1 hour)
        _attempts[ip] = [t for t in _attempts[ip] if now - t < 3600]
        if len(_attempts[ip]) >= 5:
            return render_template("pin.html", error="Too many attempts. Please wait 1 hour.")
        _attempts[ip].append(now)

        if request.form.get("pin") == APP_PIN:
            session["pin_ok"] = True
            if both_valid():
                session["auth_ready"] = True
                return redirect(request.args.get("next", "/"))
            return redirect("/auth/status")

        return render_template("pin.html", error="Wrong PIN. Please try again.")

    return render_template("pin.html", error=None)


@bp.route("/auth/status")
def auth_status():
    """Show Dhan + Telegram credential health. Sets session["auth_ready"] when both ok."""
    dhan_info = check_dhan_token()
    tg_info   = check_tg_session()
    all_ok    = dhan_info["valid"] and tg_info["valid"]

    if all_ok:
        session["auth_ready"] = True
    else:
        session.pop("auth_ready", None)

    return render_template(
        "auth_status.html",
        dhan=dhan_info,
        tg=tg_info,
        all_ok=all_ok,
    )
