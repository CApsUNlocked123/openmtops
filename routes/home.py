from flask import Blueprint, render_template
from dhan_broker import dhan

bp = Blueprint("home", __name__)


@bp.route("/")
def index():
    try:
        pos = dhan.get_positions()
        if pos.get("status") == "success":
            open_count = len(pos.get("data", []))
            dhan_status = {"ok": True, "count": open_count}
        else:
            dhan_status = {"ok": False}
    except Exception:
        dhan_status = {"ok": False}

    return render_template("home.html", dhan=dhan_status)
