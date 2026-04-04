from flask import Blueprint, render_template, request, redirect, session, flash
from math import floor
from dhan import dhan, lookup_security

bp = Blueprint("custom", __name__)

INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]


@bp.route("/custom", methods=["GET", "POST"])
def custom_trade():
    if request.method == "POST":
        instrument  = request.form.get("instrument", "").upper()
        strike      = request.form.get("strike", "").strip()
        option_type = request.form.get("option_type", "CE").upper()
        entry       = request.form.get("entry", "").strip()
        sl          = request.form.get("sl", "").strip()
        targets_raw = request.form.get("targets", "").strip()
        lots_mode   = request.form.get("lots_mode", "auto")
        lots_manual = request.form.get("lots_manual", "").strip()

        # Validate
        if not entry or not targets_raw:
            flash("Entry and at least one target are required.", "warning")
            return render_template("custom.html", indices=INDICES, form=request.form)

        targets = [t.strip() for t in targets_raw.split(",") if t.strip()]

        sec = lookup_security(instrument, strike, option_type)
        if not sec:
            flash(f"No contract found for {instrument} {strike} {option_type}.", "warning")
            return render_template("custom.html", indices=INDICES, form=request.form)

        # Lots calculation
        lot_size = sec["lot_size"]
        if lots_mode == "manual" and lots_manual:
            lots_override = int(lots_manual)
        else:
            lots_override = None
            try:
                resp  = dhan.get_fund_limits()
                funds = float(resp["data"]["availabelBalance"]) if resp.get("status") == "success" else 0
                lots_override = floor(funds / (float(entry) * lot_size)) if float(entry) > 0 else 0
            except Exception:
                lots_override = 0

        session["watching"] = {
            "security_id":    sec["security_id"],
            "trading_symbol": sec["trading_symbol"],
            "expiry":         sec["expiry"],
            "lot_size":       lot_size,
            "exchange_segment": sec["exchange_segment"],
            "entry":          entry,
            "sl":             sl,
            "targets":        targets,
            "lots_override":  lots_override,
        }
        return redirect("/live")

    import os
    prefill = {}
    if os.getenv("TESTING") == "1":
        from testing.dummy_data import SECURITY, TICK_ENTRY, TICK_SL, TICK_T1, TICK_T2
        prefill = {
            "instrument":  "NIFTY",
            "strike":      "25100",
            "option_type": "CE",
            "entry":       str(TICK_ENTRY),
            "sl":          str(TICK_SL),
            "targets":     f"{TICK_T1},{TICK_T2}",
            "lots_mode":   "manual",
            "lots_manual": "1",
        }
    return render_template("custom.html", indices=INDICES, form=prefill)
