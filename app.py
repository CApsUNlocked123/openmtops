"""
Dhan Trading — Flask + Flask-SocketIO entry point.
Run:  python app.py
"""

import os
from flask import Flask, redirect, session, request
from extensions import socketio

import routes.home       as home_mod
import routes.live       as live_mod
import routes.tips       as tips_mod
import routes.custom     as custom_mod
import routes.analyzer   as analyzer_mod
import routes.history    as history_mod
import routes.settings   as settings_mod
import routes.oi_tracker  as oi_tracker_mod
import routes.dashboard   as dashboard_mod
import routes.auth        as auth_mod
import routes.notifications as notif_mod
import candle_service
import notification_service


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY") or "dhan-local-secret-2024"

    socketio.init_app(app, async_mode="threading", cors_allowed_origins="*")

    # Start background services (daemon threads)
    candle_service.start()
    notification_service.start(socketio)

    # ── Blueprints — auth first so its routes take priority ───────────────────
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
    }

    @app.before_request
    def _auth_guard():
        path = request.path
        if path.startswith("/static"):
            return
        if path in PUBLIC_PATHS:
            return
        # API polling: skip auth_ready so in-page widgets don't break mid-session
        if path.startswith("/api/"):
            return
        # PIN gate
        if os.getenv("APP_PIN") and not session.get("pin_ok"):
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
