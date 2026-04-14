"""
Scanner — live strategy signal viewer + tips browser with one-click execute to /activetrade.

Signals are polled client-side from /api/dashboard/snapshot.
Tips are fetched server-side from Telegram (same source as /tips).
"""

from flask import Blueprint, render_template, request, redirect, session, flash
from telegram_client import get_tips
from dhan_broker import lookup_security
from candle_service import INSTRUMENT_NAMES

bp = Blueprint("scanner", __name__)


@bp.route("/scanner")
def scanner_redirect():
    """Legacy URL — permanent redirect to new primary /scan."""
    return redirect("/scan", 301)


@bp.route("/scanner/page")
def scanner_page():
    limit = int(request.args.get("limit", 20))
    try:
        tips = get_tips(limit=limit)
    except Exception as e:
        flash(f"Could not load tips: {e}", "warning")
        tips = []
    return render_template(
        "scanner.html",
        tips=tips,
        instruments=INSTRUMENT_NAMES,
        limit=limit,
    )


@bp.route("/scanner/execute", methods=["POST"])
def scanner_execute():
    """Execute a tip directly from the scanner — like /tips/execute but → /activetrade."""
    symbol      = request.form.get("symbol", "").upper()
    strike      = request.form.get("strike", "")
    option_type = request.form.get("option_type", "").upper()
    entry       = request.form.get("entry", "")
    sl          = request.form.get("sl", "")
    targets_raw = request.form.get("targets", "")

    sec = lookup_security(symbol, strike, option_type)
    if not sec:
        flash("Security not found in instrument master.", "warning")
        return redirect("/scan")

    targets = [t.strip() for t in targets_raw.split(",") if t.strip()]

    session["watching"] = {
        "security_id":      sec["security_id"],
        "trading_symbol":   sec["trading_symbol"],
        "expiry":           sec["expiry"],
        "lot_size":         sec["lot_size"],
        "exchange_segment": sec["exchange_segment"],
        "entry":            entry,
        "sl":               sl,
        "targets":          targets,
        "mode":             "single",
    }
    return redirect("/trade")
