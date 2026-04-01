from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from tgwrap import get_tips
from dhan import dhan, lookup_security

bp = Blueprint("tips", __name__)


@bp.route("/tips")
def tips_page():
    limit  = int(request.args.get("limit", 50))
    cached = session.get("tips_cache_limit")

    if "tips" not in session or cached != limit:
        try:
            tips = get_tips(limit=limit)
            session["tips"] = tips
            session["tips_cache_limit"] = limit
        except Exception as e:
            flash(f"Error fetching tips: {e}", "danger")
            session["tips"] = []

    tips = session.get("tips", [])
    return render_template("tips.html", tips=tips, limit=limit)


@bp.route("/tips/refresh")
def refresh_tips():
    session.pop("tips", None)
    session.pop("tips_cache_limit", None)
    limit = int(request.args.get("limit", 50))
    return redirect(f"/tips?limit={limit}")


@bp.route("/tips/lookup", methods=["POST"])
def lookup_tip():
    data   = request.json or {}
    symbol = data.get("symbol", "").upper()
    strike = data.get("strike", "")
    otype  = data.get("type", "").upper()
    sec    = lookup_security(symbol, strike, otype)
    if sec:
        return jsonify({"found": True, **sec})
    return jsonify({"found": False})


@bp.route("/tips/execute", methods=["POST"])
def execute_tip():
    symbol      = request.form.get("symbol", "").upper()
    strike      = request.form.get("strike", "")
    option_type = request.form.get("option_type", "").upper()
    entry       = request.form.get("entry", "")
    sl          = request.form.get("sl", "")
    targets_raw = request.form.get("targets", "")

    sec = lookup_security(symbol, strike, option_type)
    if not sec:
        flash("Security not found in instrument master.", "warning")
        return redirect("/tips")

    targets = [t.strip() for t in targets_raw.split(",") if t.strip()]

    session["watching"] = {
        "security_id":    sec["security_id"],
        "trading_symbol": sec["trading_symbol"],
        "expiry":         sec["expiry"],
        "lot_size":       sec["lot_size"],
        "entry":          entry,
        "sl":             sl,
        "targets":        targets,
    }
    return redirect("/live")
