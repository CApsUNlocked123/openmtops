import os
import sys
import threading
from flask import Blueprint, render_template, request, redirect, session, flash
from tgwrap import is_authorized, send_code, complete_sign_in
from runtime_config import get_dhan_credentials, save_dhan_credentials
from dhan import dhan

bp = Blueprint("settings", __name__)


@bp.route("/settings")
def settings():
    authed = is_authorized()
    if authed:
        session["tg_authorized"] = True
    client_id, token = get_dhan_credentials()
    masked_token = (token[:8] + "..." + token[-4:]) if len(token) > 12 else "***"
    return render_template(
        "settings.html",
        tg_authorized=authed,
        tg_step=session.get("tg_step", "phone"),
        client_id=client_id,
        masked_token=masked_token,
    )


# ── Telegram auth wizard ───────────────────────────────────────────────────────

@bp.route("/settings/tg/phone", methods=["POST"])
def tg_phone():
    phone = request.form.get("phone", "").strip()
    if not phone:
        flash("Phone number required.", "warning")
        return redirect("/settings")
    try:
        code_hash = send_code(phone)
        session["tg_phone"] = phone
        session["tg_hash"]  = code_hash
        session["tg_step"]  = "code"
    except Exception as e:
        flash(f"Failed to send code: {e}", "danger")
    return redirect("/settings")


@bp.route("/settings/tg/code", methods=["POST"])
def tg_code():
    code  = request.form.get("code", "").strip()
    phone = session.get("tg_phone", "")
    hash_ = session.get("tg_hash", "")
    if not code:
        flash("Code required.", "warning")
        return redirect("/settings")
    result = complete_sign_in(phone, code, hash_)
    if result == "ok":
        session["tg_authorized"] = True
        session.pop("tg_step", None)
        flash("Telegram authenticated successfully.", "success")
        return redirect("/auth/status")
    elif result == "2fa":
        session["tg_step"] = "password"
        flash("2-FA password required.", "info")
    else:
        flash(f"Auth failed: {result}", "danger")
    return redirect("/settings")


@bp.route("/settings/tg/2fa", methods=["POST"])
def tg_2fa():
    password = request.form.get("password", "").strip()
    phone    = session.get("tg_phone", "")
    hash_    = session.get("tg_hash", "")
    result   = complete_sign_in(phone, "", hash_, password=password)
    if result == "ok":
        session["tg_authorized"] = True
        session.pop("tg_step", None)
        flash("Telegram authenticated successfully.", "success")
        return redirect("/auth/status")
    else:
        flash(f"2-FA failed: {result}", "danger")
    return redirect("/settings")


@bp.route("/settings/tg/reauth", methods=["POST"])
def tg_reauth():
    session.pop("tg_step", None)
    session["tg_step"] = "phone"
    session["tg_authorized"] = False
    return redirect("/settings")


# ── Dhan credentials ──────────────────────────────────────────────────────────

@bp.route("/settings/dhan", methods=["POST"])
def dhan_update():
    client_id = request.form.get("client_id", "").strip()
    token     = request.form.get("access_token", "").strip()
    if client_id and token:
        save_dhan_credentials(client_id, token)
        return redirect("/settings/restarting")
    else:
        flash("Both Client ID and Access Token are required.", "warning")
    return redirect("/settings")


@bp.route("/settings/restarting")
def restarting():
    """Show a 'restarting' page, then restart the process after 1 s."""
    def _do_restart():
        import time, subprocess
        time.sleep(1)
        subprocess.Popen([sys.executable, '-m', 'flask'] + sys.argv[1:])
        os._exit(0)

    threading.Thread(target=_do_restart, daemon=True).start()
    return render_template("restarting.html")


@bp.route("/settings/dhan/test", methods=["POST"])
def dhan_test():
    try:
        resp = dhan.get_positions()
        if resp.get("status") == "success":
            flash(f"Dhan connected. {len(resp.get('data', []))} open positions.", "success")
        else:
            flash("Dhan API returned an error. Check credentials.", "danger")
    except Exception as e:
        flash(f"Connection failed: {e}", "danger")
    return redirect("/settings")
