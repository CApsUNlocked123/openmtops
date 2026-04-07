from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from tgwrap import get_tips
from dhan import dhan, lookup_security
import os

bp = Blueprint("tips", __name__)

# ── External API ───────────────────────────────────────────────────────────────

@bp.route("/api/tips")
def api_tips():
    """
    JSON endpoint for external apps (e.g. Flutter).

    Query params:
      limit    int  Max messages to scan (default 50).
      refresh  any  If present, bypass the server-side cache.
      key      str  Optional API key check (set API_KEY in .env to enable).

    Response 200:
      { "ok": true, "tips": [ { symbol, strike, type, entry, sl,
                                 targets, raw, date, msg_id } ] }
    Response 401:
      { "ok": false, "error": "Unauthorized" }
    Response 500:
      { "ok": false, "error": "<message>" }
    """
    api_key = os.getenv("API_KEY")
    if api_key:
        provided = request.headers.get("X-Api-Key") or request.args.get("key")
        if provided != api_key:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

    limit = int(request.args.get("limit", 50))
    cache_key = f"api_tips_{limit}"

    if "refresh" not in request.args and cache_key in session:
        return jsonify({"ok": True, "tips": session[cache_key], "cached": True})

    try:
        tips = get_tips(limit=limit)
        session[cache_key] = tips
        return jsonify({"ok": True, "tips": tips, "cached": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
        "exchange_segment": sec["exchange_segment"],
        "entry":          entry,
        "sl":             sl,
        "targets":        targets,
    }
    return redirect("/live")
