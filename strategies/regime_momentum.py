"""
RegimeMomentum strategy widget.

Wraps signal_engine.generate_signal() via the existing /api/dashboard/snapshot
endpoint — never imports signal_engine or dashboard.py directly (constraint preserved).
"""

import json
import urllib.request

from .base import StrategyWidget, SignalResult

DISPLAY_NAME = "Regime Momentum"   # legacy module-level attr (used by old scan.py)
DESCRIPTION  = "Signal from regime/phase/velocity/health indicators via signal_engine"

_SNAPSHOT_URL = "http://127.0.0.1:5000/api/dashboard/snapshot"


def snapshot(instrument: str) -> dict:
    """Legacy function-level API kept for backward compat with old routes/scan.py."""
    return RegimeMomentumWidget()._fetch(instrument)


class RegimeMomentumWidget(StrategyWidget):
    slug        = "regime_momentum"
    name        = "Regime Momentum"
    icon        = "🧠"
    description = DESCRIPTION

    # ── Internal ──────────────────────────────────────────────────────────

    def _fetch(self, instrument: str) -> dict:
        url = f"{_SNAPSHOT_URL}?instrument={instrument}"
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                data = json.loads(resp.read().decode())
            sig = data.get("signal") or data
            return SignalResult(
                action          = sig.get("action", "WAIT"),
                direction       = sig.get("direction"),
                instrument      = sig.get("instrument", instrument),
                entry           = sig.get("entry"),
                target          = sig.get("target"),
                sl              = sig.get("sl"),
                regime          = sig.get("regime", "—"),
                phase           = sig.get("phase", "—"),
                health_score    = sig.get("health_score", 0),
                lin_score       = sig.get("lin_score", 0),
                reason          = sig.get("reason", ""),
                counter_reasons = sig.get("counter_reasons", []),
                generated_at    = sig.get("generated_at", ""),
            ).to_dict()
        except Exception as exc:
            return SignalResult(action="WAIT", instrument=instrument,
                                error=str(exc)).to_dict()

    # ── StrategyWidget contract ────────────────────────────────────────────

    def initial_data(self, instrument: str, snapshot: dict) -> dict:
        return self._fetch(instrument)

    def poll_data(self, instrument: str, snapshot: dict) -> dict:
        return self._fetch(instrument)
