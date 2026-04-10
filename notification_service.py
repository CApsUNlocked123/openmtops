"""
Standalone notification service.

Any module can post a notification:
    from notification_service import notify
    notify("Signal", "NIFTY score 82 — IMPULSE_UP", category="signal",
           instrument="NIFTY", send_telegram=True)

Notifications appear in the nav-bar bell dropdown in real time via SocketIO.
A background thread polls Telegram tips every 60 s and surfaces new ones here.
"""

import threading
import logging
import uuid
from datetime import datetime

log = logging.getLogger(__name__)

CATEGORIES = {"signal", "tip", "alert", "system"}
MAX_NOTIFICATIONS = 50

_lock:          threading.Lock = threading.Lock()
_notifications: list           = []   # newest first
_seen_tip_ids:  set            = set()
_sio                           = None   # injected by start()


# ── public API ────────────────────────────────────────────────────────────────

def notify(title: str, body: str, category: str = "alert",
           instrument: str = None, send_telegram: bool = False) -> None:
    """Post a notification. Thread-safe. Emits SocketIO 'notification' event."""
    if category not in CATEGORIES:
        category = "alert"

    notif = {
        "id":         str(uuid.uuid4()),
        "title":      title,
        "body":       body,
        "category":   category,
        "instrument": instrument,
        "time":       datetime.now().strftime("%H:%M"),
        "read":       False,
    }

    with _lock:
        _notifications.insert(0, notif)
        if len(_notifications) > MAX_NOTIFICATIONS:
            _notifications.pop()

    # Emit to all connected browser clients
    if _sio:
        try:
            _sio.emit("notification", notif)
        except Exception as e:
            log.error("[notification_service] SocketIO emit error: %s", e)

    # Optional Telegram self-message (Saved Messages)
    if send_telegram:
        _send_telegram(f"*{title}*\n{body}")


def get_all() -> list:
    with _lock:
        return list(_notifications)


def mark_read(notification_id: str = None) -> None:
    """Mark one notification as read (or all if id is None)."""
    with _lock:
        for n in _notifications:
            if notification_id is None or n["id"] == notification_id:
                n["read"] = True


def get_unread_count() -> int:
    with _lock:
        return sum(1 for n in _notifications if not n["read"])


def start(sio) -> None:
    """Inject SocketIO instance and start background tips poller."""
    global _sio
    _sio = sio
    t = threading.Thread(target=_tips_poller, daemon=True, name="notif-tips-poller")
    t.start()
    log.info("[notification_service] started")


# ── internals ─────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> None:
    """Best-effort Telegram Saved Messages delivery (stub — extend if needed)."""
    log.info("[notification_service] send_telegram stub: %s", text[:80])


def _tips_poller() -> None:
    """Poll Telegram tips every 60 s and surface new ones as notifications."""
    import time
    while True:
        try:
            _poll_tips_once()
        except Exception as e:
            log.debug("[notification_service] tips poll error: %s", e)
        time.sleep(60)


def _poll_tips_once() -> None:
    try:
        import telegram_client
        # get_tips returns list of {"msg_id", "raw", "symbol", "date", ...}
        tips = telegram_client.get_tips(limit=20)
        if not tips:
            return
        for tip in tips:
            tip_id = str(tip.get("msg_id") or "")
            if not tip_id or tip_id in _seen_tip_ids:
                continue
            _seen_tip_ids.add(tip_id)
            raw = tip.get("raw") or ""
            symbol = tip.get("symbol") or ""
            title = f"New Tip — {symbol}" if symbol else "New Tip"
            notify(
                title=title,
                body=raw[:120],
                category="tip",
                instrument=symbol or None,
                send_telegram=False,
            )
    except Exception as e:
        log.debug("[notification_service] _poll_tips_once: %s", e)
