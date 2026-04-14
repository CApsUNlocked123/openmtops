"""
First-run setup wizard for OpenMTOps.

Guides new VM users through:
  Step 1 — Telegram API credentials (api_id + api_hash)
  Step 2 — Telegram phone authentication (phone → OTP → optional 2FA)
  Step 3 — Dhan broker credentials (client_id + access_token)
  Step 4 — Optional settings (APP_PIN + Telegram channel ID)
  /setup/complete — writes .env for restart persistence

The wizard guard in app.py redirects ALL paths to /setup until
is_configured() returns True.  Once configured the wizard is
permanently bypassed (is_configured() reads .env via os.environ).
"""

from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from runtime_config import (
    _load, set_many, is_configured, flush_to_dotenv,
    get_telegram_credentials,
)
from telegram_client import is_authorized, send_code, complete_sign_in

bp = Blueprint("setup", __name__)


# ── Smart redirect ────────────────────────────────────────────────────────────

@bp.route("/setup")
def setup_index():
    if is_configured():
        return redirect("/")
    cfg = _load()
    tg   = cfg.get("telegram", {})
    dhan = cfg.get("dhan", {})
    # Resume to the right step
    if not (tg.get("api_id") or tg.get("skipped")):
        return redirect("/setup/step/1")
    elif not (is_authorized() or tg.get("skipped")):
        return redirect("/setup/step/2")
    elif not dhan.get("client_id"):
        return redirect("/setup/step/3")
    else:
        return redirect("/setup/step/4")


# ── Step 1: Telegram API credentials ─────────────────────────────────────────

@bp.route("/setup/step/1", methods=["GET", "POST"])
def setup_step1():
    if is_configured():
        return redirect("/")

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "skip_telegram":
            set_many({"telegram.skipped": True, "telegram.api_id": 0, "telegram.api_hash": ""})
            return redirect("/setup/step/3")

        api_id   = request.form.get("api_id",   "").strip()
        api_hash = request.form.get("api_hash", "").strip()

        if not api_id or not api_id.isdigit():
            flash("API ID must be a number (find it at my.telegram.org).", "danger")
            return redirect("/setup/step/1")
        if not api_hash or len(api_hash) < 16:
            flash("API Hash looks invalid — check my.telegram.org.", "danger")
            return redirect("/setup/step/1")

        set_many({"telegram.api_id": int(api_id), "telegram.api_hash": api_hash, "telegram.skipped": False})
        return redirect("/setup/step/2")

    cfg = _load()
    saved_id   = cfg.get("telegram", {}).get("api_id", "")
    saved_hash = cfg.get("telegram", {}).get("api_hash", "")
    return render_template("setup_step1.html", saved_id=saved_id, saved_hash=saved_hash)


# ── Step 2: Telegram phone auth ───────────────────────────────────────────────

@bp.route("/setup/step/2", methods=["GET"])
def setup_step2():
    if is_configured():
        return redirect("/")
    # If already authorised (e.g. anon.session present), skip straight to step 3
    if is_authorized():
        return redirect("/setup/step/3")
    tg_step = session.get("tg_step", "phone")
    return render_template("setup_step2.html", tg_step=tg_step)


@bp.route("/setup/tg/phone", methods=["POST"])
def setup_tg_phone():
    phone = request.form.get("phone", "").strip()
    if not phone:
        flash("Phone number required.", "warning")
        return redirect("/setup/step/2")
    try:
        code_hash = send_code(phone)
        session["tg_phone"] = phone
        session["tg_hash"]  = code_hash
        session["tg_step"]  = "code"
    except Exception as e:
        flash(f"Failed to send code: {e}", "danger")
    return redirect("/setup/step/2")


@bp.route("/setup/tg/code", methods=["POST"])
def setup_tg_code():
    code  = request.form.get("code", "").strip()
    phone = session.get("tg_phone", "")
    hash_ = session.get("tg_hash", "")
    if not code:
        flash("Verification code required.", "warning")
        return redirect("/setup/step/2")
    result = complete_sign_in(phone, code, hash_)
    if result == "ok":
        session.pop("tg_step", None)
        session["tg_authorized"] = True
        flash("Telegram connected.", "success")
        return redirect("/setup/step/3")
    elif result == "2fa":
        session["tg_step"] = "password"
        flash("2-FA password required.", "info")
    else:
        flash(f"Auth failed: {result}", "danger")
    return redirect("/setup/step/2")


@bp.route("/setup/tg/2fa", methods=["POST"])
def setup_tg_2fa():
    password = request.form.get("password", "").strip()
    phone    = session.get("tg_phone", "")
    hash_    = session.get("tg_hash", "")
    result   = complete_sign_in(phone, "", hash_, password=password)
    if result == "ok":
        session.pop("tg_step", None)
        session["tg_authorized"] = True
        flash("Telegram connected.", "success")
        return redirect("/setup/step/3")
    else:
        flash(f"2-FA failed: {result}", "danger")
    return redirect("/setup/step/2")


# ── Step 3: Dhan credentials ──────────────────────────────────────────────────

@bp.route("/setup/step/3", methods=["GET", "POST"])
def setup_step3():
    if is_configured():
        return redirect("/")

    if request.method == "POST":
        client_id = request.form.get("client_id",    "").strip()
        token     = request.form.get("access_token", "").strip()
        api_key   = request.form.get("api_key",      "").strip()
        api_secret = request.form.get("api_secret",  "").strip()

        if not client_id or not token:
            flash("Client ID and Access Token are required.", "danger")
            return redirect("/setup/step/3")

        set_many({
            "dhan.client_id":    client_id,
            "dhan.access_token": token,
            "dhan.api_key":      api_key,
            "dhan.api_secret":   api_secret,
        })
        return redirect("/setup/step/4")

    cfg = _load()
    dhan = cfg.get("dhan", {})
    return render_template("setup_step3.html",
                           saved_client_id=dhan.get("client_id", ""),
                           saved_api_key=dhan.get("api_key", ""),
                           saved_api_secret=dhan.get("api_secret", ""))


@bp.route("/setup/step/3/test", methods=["POST"])
def setup_step3_test():
    """AJAX: test Dhan credentials without persisting them."""
    client_id = request.json.get("client_id", "").strip()
    token     = request.json.get("access_token", "").strip()
    if not client_id or not token:
        return jsonify({"ok": False, "error": "Missing credentials"})
    try:
        from dhanhq import DhanContext, dhanhq
        ctx  = DhanContext(client_id, token)
        d    = dhanhq(ctx)
        resp = d.get_positions()
        if resp.get("status") == "success":
            count = len(resp.get("data", []))
            return jsonify({"ok": True, "message": f"Connected — {count} open position(s)"})
        return jsonify({"ok": False, "error": resp.get("remarks", "API error")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Step 4: Optional settings ─────────────────────────────────────────────────

@bp.route("/setup/step/4", methods=["GET", "POST"])
def setup_step4():
    if is_configured():
        return redirect("/")

    if request.method == "POST":
        pin        = request.form.get("app_pin", "").strip()
        channel_id = request.form.get("channel_id", "").strip()

        updates = {"app_pin": pin}
        if channel_id:
            try:
                updates["telegram_channel_id"] = int(channel_id)
            except ValueError:
                flash("Channel ID must be a number (e.g. -1001234567890).", "warning")
                return redirect("/setup/step/4")
        set_many(updates)
        return redirect("/setup/complete")

    from runtime_config import get_telegram_channel_id
    default_channel = get_telegram_channel_id()
    return render_template("setup_step4.html", default_channel=default_channel)


# ── Complete ──────────────────────────────────────────────────────────────────

@bp.route("/setup/complete")
def setup_complete():
    # Flush config.json → .env so credentials survive restarts
    flush_to_dotenv()
    # Refresh auth session so app doesn't re-check auth immediately
    session["auth_ready"] = True

    cfg = _load()
    tg   = cfg.get("telegram", {})
    dhan = cfg.get("dhan", {})
    summary = {
        "telegram_done": bool(tg.get("api_id") or tg.get("skipped")),
        "telegram_skipped": tg.get("skipped", False),
        "dhan_done":     bool(dhan.get("client_id")),
        "pin_set":       bool(cfg.get("app_pin")),
        "channel_set":   bool(cfg.get("telegram_channel_id")),
    }
    return render_template("setup_complete.html", summary=summary)
