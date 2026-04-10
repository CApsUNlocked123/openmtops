import os
from flask import Blueprint, render_template, request, redirect, session, flash
from telegram_client import is_authorized, send_code, complete_sign_in
from runtime_config import get_dhan_credentials, save_dhan_credentials, set_many, flush_to_dotenv
from dhan_broker import dhan, reset_dhan

bp = Blueprint("settings", __name__)


@bp.route("/settings")
def settings():
    authed = is_authorized()
    if authed:
        session["tg_authorized"] = True
    client_id, token = get_dhan_credentials()
    masked_token = (token[:8] + "..." + token[-4:]) if len(token) > 12 else "***"

    from runtime_config import _load, get_telegram_channel_id, get as _cfg
    cfg = _load()
    tg  = cfg.get("telegram", {})

    return render_template(
        "settings.html",
        tg_authorized=authed,
        tg_step=session.get("tg_step", "phone"),
        client_id=client_id,
        masked_token=masked_token,
        tg_api_id=tg.get("api_id", ""),
        tg_api_hash=tg.get("api_hash", ""),
        current_channel_id=get_telegram_channel_id(),
        pin_set=bool(_cfg("app_pin")),
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
        save_dhan_credentials(client_id, token)  # writes config.json + flushes .env
        reset_dhan()  # force re-init on next Dhan API call (no restart needed)
        flash("Dhan credentials updated successfully.", "success")
    else:
        flash("Both Client ID and Access Token are required.", "warning")
    return redirect("/settings")


@bp.route("/settings/restarting")
def restarting():
    """Legacy restart page — kept so old bookmarks don't 404. Redirects to settings."""
    return redirect("/settings")


# ── Telegram API credentials ──────────────────────────────────────────────────

@bp.route("/settings/telegram/api", methods=["POST"])
def telegram_api_update():
    api_id   = request.form.get("api_id",   "").strip()
    api_hash = request.form.get("api_hash", "").strip()
    if api_id and api_hash:
        set_many({"telegram.api_id": int(api_id), "telegram.api_hash": api_hash})
        flush_to_dotenv()
        flash("Telegram API credentials updated. Re-authenticate below.", "info")
    else:
        flash("Both API ID and API Hash are required.", "warning")
    return redirect("/settings")


@bp.route("/settings/telegram/channel", methods=["POST"])
def telegram_channel_update():
    channel_id = request.form.get("channel_id", "").strip()
    if channel_id:
        try:
            set_many({"telegram_channel_id": int(channel_id)})
            flush_to_dotenv()
            flash("Telegram channel updated.", "success")
        except ValueError:
            flash("Channel ID must be a number.", "warning")
    return redirect("/settings")


@bp.route("/settings/pin", methods=["POST"])
def pin_update():
    pin = request.form.get("pin", "").strip()
    set_many({"app_pin": pin})
    flush_to_dotenv()
    flash("PIN updated." if pin else "PIN disabled.", "success")
    return redirect("/settings")


# ── Dhan test connection ──────────────────────────────────────────────────────

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
