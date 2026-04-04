"""
Signal notifier — background thread that scans the strategy dashboard
indicators every 30 minutes during market hours and fires a notification
when a high-confidence ENTER signal is detected.

Call signal_notifier.start() from app.py after notification_service.start().
"""

import threading
import logging
import time
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

_COOLDOWN_SECONDS = 30 * 60   # 30-minute minimum between notifications per instrument
_last_notified: dict = {}      # instrument → epoch float

IST = timezone(timedelta(hours=5, minutes=30))

INSTRUMENTS = ["NIFTY", "BANKNIFTY"]


def start() -> None:
    t = threading.Thread(target=_scanner_loop, daemon=True, name="signal-notifier")
    t.start()
    log.info("[signal_notifier] started")


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:   # Saturday/Sunday
        return False
    t = now.time()
    return (9, 15) <= (t.hour, t.minute) <= (15, 30)


def _scanner_loop() -> None:
    while True:
        if _is_market_hours():
            for instrument in INSTRUMENTS:
                try:
                    _check_instrument(instrument)
                except Exception as e:
                    log.debug("[signal_notifier] %s error: %s", instrument, e)
        time.sleep(5 * 60)   # check every 5 minutes


def _check_instrument(instrument: str) -> None:
    from routes.dashboard import _build_snapshot

    snap = _build_snapshot(instrument)
    signals = snap.get("signals") or []
    if not signals:
        return

    # Look for a high-confidence ENTER signal
    enter = next((s for s in signals if "ENTER" in s.get("action", "").upper()), None)
    if not enter:
        return

    score = enter.get("score", 0)
    if score < 70:
        return

    now = time.time()
    last = _last_notified.get(instrument, 0)
    if now - last < _COOLDOWN_SECONDS:
        return

    _last_notified[instrument] = now
    regime = snap.get("regime", "")
    phase  = snap.get("phase", "")
    spot   = snap.get("spot", 0)

    from notification_service import notify
    notify(
        title=f"ENTER Signal — {instrument}",
        body=f"Score {score}/100 · {regime} · {phase} · {spot:.0f}",
        category="signal",
        instrument=instrument,
        send_telegram=True,
    )
