"""
Live market feed using DhanHQ MarketFeed WebSocket.
Uses the library's built-in callback pattern + start() for background threading.
"""

import time
from dhanhq import MarketFeed

# ── Shared state (thread → Streamlit) ────────────────────────────────────────
price_cache: dict = {}   # {security_id_str: tick_dict, "__status__": ..., ...}

_feed: MarketFeed | None = None


# ── Public API ────────────────────────────────────────────────────────────────

def start_feed(dhan_context, instruments: list[tuple], on_tick=None):
    """
    Start WebSocket feed for a list of (exchange, security_id, sub_type) tuples.
    Restarts any existing feed.

    Example:
        start_feed(dhan_context, [
            (MarketFeed.NSE_FNO, "12345", MarketFeed.Full),
            (MarketFeed.NSE_FNO, "67890", MarketFeed.Full),
        ])
    """
    global _feed

    stop_feed()

    def _on_connect(feed):
        print(f"[feed] connected to DhanHQ WebSocket")
        price_cache["__status__"] = "connected"

    def _on_message(feed, tick):
        print(f"[feed] tick received: {tick!r}")
        if tick and "security_id" in tick:
            sid = str(tick["security_id"])
            price_cache[sid] = tick
            if on_tick:
                try:
                    on_tick(sid, tick)
                except Exception as e:
                    print(f"[feed] on_tick error: {e}")

    def _on_error(feed, exc):
        msg = str(exc)
        if "no close frame" in msg or "no close frame" in msg.lower():
            return  # dhanhq keepalive quirk — ignore
        print(f"[feed] ERROR: {msg}")
        price_cache["__error__"] = msg
        price_cache["__status__"] = "reconnecting"
        if "429" in msg:
            print("[feed] Rate limited (429) — stopping feed for 30s")
            feed._running = False
            import threading
            def _restart():
                time.sleep(30)
                if price_cache.get("__status__") != "disconnected":
                    feed._running = True
                    import asyncio
                    asyncio.run_coroutine_threadsafe(feed._run_async(), feed.loop)
            threading.Thread(target=_restart, daemon=True).start()

    def _on_close(feed):
        if price_cache.get("__status__") != "disconnected":
            price_cache["__status__"] = "reconnecting"

    price_cache["__status__"]          = "connecting"
    price_cache["__reconnect_count__"] = 0
    price_cache.pop("__error__", None)

    _feed = MarketFeed(
        dhan_context, instruments, "v2",
        on_connect=_on_connect,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )
    _feed.start()   # runs in a daemon thread with auto-reconnect loop


def stop_feed():
    """Stop the feed and clear the cache."""
    global _feed
    if _feed:
        try:
            _feed.close_connection()
        except Exception:
            pass
        _feed = None
    price_cache["__status__"] = "disconnected"
    # keep ticks in cache so the UI doesn't flash on restart


def get_tick(security_id: str) -> dict | None:
    """Return the latest full tick dict for a security_id, or None."""
    return price_cache.get(str(security_id))


def get_ltp(security_id: str) -> float | None:
    entry = price_cache.get(str(security_id))
    if not entry:
        return None
    return float(entry.get("LTP") or entry.get("last_price") or 0) or None


def feed_status() -> str:
    """Return current feed status: connecting | connected | reconnecting | disconnected."""
    return price_cache.get("__status__", "disconnected")


def is_connected() -> bool:
    return feed_status() == "connected"


def reconnect_count() -> int:
    return price_cache.get("__reconnect_count__", 0)


def last_error() -> str | None:
    return price_cache.get("__error__")
