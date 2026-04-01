"""
Dhan Trading — Flask + Flask-SocketIO entry point.
Run:  python app.py
"""

from flask import Flask, redirect, session, request
from extensions import socketio
from tgwrap import is_authorized

import routes.home       as home_mod
import routes.live       as live_mod
import routes.tips       as tips_mod
import routes.custom     as custom_mod
import routes.analyzer   as analyzer_mod
import routes.history    as history_mod
import routes.settings   as settings_mod
import routes.oi_tracker as oi_tracker_mod


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "dhan-local-secret-2024"

    socketio.init_app(app, async_mode="threading", cors_allowed_origins="*")

    # ── Blueprints ─────────────────────────────────────────────────────────────
    app.register_blueprint(home_mod.bp)
    app.register_blueprint(live_mod.bp)
    app.register_blueprint(tips_mod.bp)
    app.register_blueprint(custom_mod.bp)
    app.register_blueprint(analyzer_mod.bp)
    app.register_blueprint(history_mod.bp)
    app.register_blueprint(settings_mod.bp)
    app.register_blueprint(oi_tracker_mod.bp)

    # ── Register SocketIO events from route modules ────────────────────────────
    live_mod.register_socketio(socketio)
    analyzer_mod.register_socketio(socketio)
    oi_tracker_mod.register_socketio(socketio)

    # ── Telegram auth guard ────────────────────────────────────────────────────
    @app.before_request
    def _tg_guard():
        PUBLIC = {"/settings", "/settings/tg/phone", "/settings/tg/code",
                  "/settings/tg/2fa", "/settings/dhan"}
        if request.path.startswith("/static"):
            return
        if request.path in PUBLIC:
            return
        if not session.get("tg_authorized"):
            if is_authorized():
                session["tg_authorized"] = True
            else:
                return redirect("/settings")

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000, debug=False, use_reloader=False)
