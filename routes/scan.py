"""
ActiveScan — widget-based strategy scan page.

URLs:
  GET  /scan                          → scan.html  (primary scan page)
  GET  /scan/widget/<slug>            → bare HTML fragment for widget slot
  GET  /api/scan/data/<slug>          → JSON poll update for widget JS
  POST /scan/execute                  → store session["watching"] → redirect /trade

Hard constraint: do NOT import signal_engine, dashboard.py, or live.py.
"""

from flask import (
    Blueprint, render_template, render_template_string,
    request, jsonify, redirect, session, flash,
)
from dhan_broker import lookup_security
from candle_service import INSTRUMENT_NAMES
from strategies import WIDGETS, WIDGET_MAP

bp = Blueprint("scan", __name__)

_DEFAULT_INSTRUMENT = "NIFTY"


def _get_snapshot(instrument: str) -> dict:
    """
    Fetch a dashboard snapshot for the given instrument via the existing
    /api/dashboard/snapshot endpoint.  Returns {} on failure.
    Never imports dashboard.py directly.
    """
    import json, urllib.request
    url = f"http://127.0.0.1:5000/api/dashboard/snapshot?instrument={instrument}"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


# ── Pages ─────────────────────────────────────────────────────────────────────

@bp.route("/scan")
def scan_page():
    instrument = request.args.get("instrument", _DEFAULT_INSTRUMENT).upper()
    return render_template(
        "scan.html",
        widgets=WIDGETS,
        instrument=instrument,
        instruments=INSTRUMENT_NAMES,
    )


# ── Widget HTML fragment ───────────────────────────────────────────────────────

@bp.route("/scan/widget/<slug>")
def widget_fragment(slug):
    """
    Returns a rendered HTML fragment (no <html>/<body>/<head> wrapper).
    Fetched by scan.js via fetch() → container.innerHTML.
    """
    if slug not in WIDGET_MAP:
        return f'<p class="text-danger small">Widget "{slug}" not found.</p>', 404

    instrument = request.args.get("instrument", _DEFAULT_INSTRUMENT).upper()
    widget     = WIDGET_MAP[slug]
    snapshot   = _get_snapshot(instrument)

    try:
        data = widget.initial_data(instrument, snapshot)
    except Exception as exc:
        data = {"action": "WAIT", "instrument": instrument, "error": str(exc)}

    return render_template(f"widgets/{slug}.html", data=data)


# ── JSON poll endpoint ─────────────────────────────────────────────────────────

@bp.route("/api/scan/data/<slug>")
def api_scan_data(slug):
    """
    Polled every 8 s by each widget's JS mount() interval.
    Returns JSON dict from widget.poll_data().
    """
    if slug not in WIDGET_MAP:
        return jsonify({"error": f"Widget '{slug}' not found"}), 404

    instrument = request.args.get("instrument", _DEFAULT_INSTRUMENT).upper()
    widget     = WIDGET_MAP[slug]
    snapshot   = _get_snapshot(instrument)

    try:
        data = widget.poll_data(instrument, snapshot)
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Execute (tip / signal → trade) ────────────────────────────────────────────

@bp.route("/scan/execute", methods=["POST"])
def scan_execute():
    """
    Receives the execute form from scan.html or scanner.html and stores
    session["watching"], then redirects to /trade.
    """
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
