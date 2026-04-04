"""
Feed Manager — single DhanHQ WebSocket connection shared by all subscribers.

Each subscriber registers by name with its instruments and callback.
The underlying feed is restarted only when the merged instrument list changes,
so Analyzer, OI Tracker, and Dashboard can all coexist on one connection.

Usage:
    import feed_manager
    feed_manager.subscribe("oi_tracker", instruments, on_tick=_on_tick)
    feed_manager.unsubscribe("oi_tracker")
"""

import threading
import logging

log = logging.getLogger(__name__)

_lock                    = threading.Lock()
_subscribers: dict       = {}     # owner → {"instruments": [...], "on_tick": fn}
_active_instruments: set = set()  # frozenset of (exch, sid, sub_type) in current feed


def subscribe(owner: str, instruments: list, on_tick: callable) -> None:
    """
    Register or update a named subscriber.

    owner       — unique caller string e.g. "oi_tracker", "analyzer"
    instruments — list of (exchange, security_id, sub_type) tuples
    on_tick     — fn(security_id: str, tick: dict)
    """
    with _lock:
        _subscribers[owner] = {"instruments": list(instruments), "on_tick": on_tick}
        log.info("[feed_manager] %s subscribed (%d instruments)", owner, len(instruments))
        _rebuild()


def unsubscribe(owner: str) -> None:
    """Remove a subscriber and update the feed if the instrument list changes."""
    with _lock:
        if owner in _subscribers:
            del _subscribers[owner]
            log.info("[feed_manager] %s unsubscribed", owner)
            _rebuild()


def get_status() -> dict:
    """Return current subscriber names and total instrument count."""
    with _lock:
        return {
            "subscribers":      list(_subscribers.keys()),
            "instrument_count": len(_active_instruments),
        }


# ── internal ──────────────────────────────────────────────────────────────────

def _rebuild():
    """Merge all subscriber instrument lists and restart feed only if changed."""
    import price_feed
    from dhan import dhan_context

    merged = []
    seen   = set()
    for sub in _subscribers.values():
        for instr in sub["instruments"]:
            key = (instr[0], str(instr[1]), instr[2])
            if key not in seen:
                seen.add(key)
                merged.append(instr)

    if frozenset(seen) == frozenset(_active_instruments):
        return   # no change — leave existing connection alone

    _active_instruments.clear()
    _active_instruments.update(seen)

    if not merged:
        price_feed.stop_feed()
        log.info("[feed_manager] no subscribers — feed stopped")
        return

    def _dispatch(sid: str, tick: dict):
        # Snapshot the subscriber dict to avoid mutation during iteration
        for sub in list(_subscribers.values()):
            try:
                sub["on_tick"](sid, tick)
            except Exception as e:
                log.error("[feed_manager] dispatch error: %s", e)

    price_feed.start_feed(dhan_context, merged, on_tick=_dispatch)
    log.info("[feed_manager] feed rebuilt — %d instruments, %d subscribers",
             len(merged), len(_subscribers))
