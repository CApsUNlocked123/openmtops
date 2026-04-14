"""
Dhan Trading — Flask + Flask-SocketIO entry point.
Run:  python app.py
"""

import os

# ── Testing mode: patch sys.modules BEFORE any route import sees real modules ──
# Set TESTING=1 in .env to run with dummy data (no Dhan/Telegram credentials).
if os.getenv("TESTING") == "1":
    import sys
    from testing import mock_dhan, mock_price_feed, mock_candle_service
    sys.modules["dhan_broker"]     = mock_dhan
    sys.modules["price_feed"]     = mock_price_feed
    sys.modules["candle_service"] = mock_candle_service
    print("[TESTING] mock modules injected: dhan, price_feed, candle_service")

from flask import Flask, redirect, session, request
from extensions import socketio

import routes.home        as home_mod
import routes.live        as live_mod
import routes.tips        as tips_mod
import routes.custom      as custom_mod
import routes.analyzer    as analyzer_mod
import routes.history     as history_mod
import routes.settings    as settings_mod
import routes.oi_tracker  as oi_tracker_mod
import routes.dashboard   as dashboard_mod
import routes.auth        as auth_mod
import routes.notifications as notif_mod
import routes.setup       as setup_mod
import routes.activetrade as activetrade_mod
import routes.scanner     as scanner_mod
import routes.profile     as profile_mod
import routes.scan        as scan_mod
import candle_service
import notification_service


def create_app() -> Flask:
    app = Flask(__name__)
    from runtime_config import get_secret_key
    app.secret_key = get_secret_key()

    socketio.init_app(app, async_mode="threading", cors_allowed_origins="*")

    # Start background services (daemon threads)
    if os.getenv("TESTING") != "1":
        candle_service.start()
    notification_service.start(socketio)

    # ── Blueprints — setup first, then auth, then everything else ────────────
    app.register_blueprint(setup_mod.bp)
    app.register_blueprint(auth_mod.bp)
    app.register_blueprint(home_mod.bp)
    app.register_blueprint(live_mod.bp)
    app.register_blueprint(tips_mod.bp)
    app.register_blueprint(custom_mod.bp)
    app.register_blueprint(analyzer_mod.bp)
    app.register_blueprint(history_mod.bp)
    app.register_blueprint(settings_mod.bp)
    app.register_blueprint(oi_tracker_mod.bp)
    app.register_blueprint(dashboard_mod.bp)
    app.register_blueprint(notif_mod.bp)
    app.register_blueprint(activetrade_mod.bp)
    app.register_blueprint(scanner_mod.bp)
    app.register_blueprint(profile_mod.bp)
    app.register_blueprint(scan_mod.bp)

    # ── Register SocketIO events from route modules ────────────────────────────
    live_mod.register_socketio(socketio)
    analyzer_mod.register_socketio(socketio)
    oi_tracker_mod.register_socketio(socketio)

    # ── Auth guard ────────────────────────────────────────────────────────────
    PUBLIC_PATHS = {
        "/pin", "/auth/status",
        "/settings", "/settings/tg/phone", "/settings/tg/code",
        "/settings/tg/2fa", "/settings/tg/reauth", "/settings/dhan",
        "/settings/restarting",
        "/settings/telegram/api", "/settings/telegram/channel", "/settings/pin",
        "/profile",
        "/tip",
    }

    SETUP_PATHS = {
        "/setup", "/setup/step/1", "/setup/step/2", "/setup/step/3",
        "/setup/step/4", "/setup/complete", "/setup/tg/phone",
        "/setup/tg/code", "/setup/tg/2fa", "/setup/step/3/test",
    }

    @app.before_request
    def _auth_guard():
        if os.getenv("TESTING") == "1":
            return   # bypass all auth checks in test mode
        path = request.path
        if path.startswith("/static"):
            return

        # Setup wizard guard — redirect everything until app is configured
        from runtime_config import is_configured
        if not is_configured():
            if path not in SETUP_PATHS:
                return redirect("/setup")
            return  # allow wizard paths through

        if path in PUBLIC_PATHS:
            return
        # API polling: skip auth_ready so in-page widgets don't break mid-session
        if path.startswith("/api/"):
            return
        # PIN gate
        from runtime_config import get as _cfg
        if (_cfg("app_pin") or os.getenv("APP_PIN")) and not session.get("pin_ok"):
            return redirect(f"/pin?next={path}")
        # API credential readiness (Dhan + Telegram both healthy)
        if not session.get("auth_ready"):
            from routes.auth import both_valid
            if both_valid():
                session["auth_ready"] = True
            else:
                return redirect("/auth/status")

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000, debug=False, use_reloader=False)
