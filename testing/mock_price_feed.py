"""
Mock price_feed module — replaces price_feed.py when TESTING=1.
start_feed() launches a daemon thread that walks PRICE_WALK and fires on_tick
for each step, driving the live.py state machine without a real WebSocket.
"""

import threading
import time
from datetime import datetime

from testing.dummy_data import PRICE_WALK, TICK_INTERVAL_S, SECURITY

price_cache: dict = {}
_stop = threading.Event()
_sim_thread: threading.Thread | None = None


def start_feed(dhan_context, instruments: list, on_tick=None):
    global _sim_thread
    _stop.clear()

    # Derive the security_id that was subscribed — first instrument in the list
    sid = str(instruments[0][1]) if instruments else SECURITY["security_id"]
    price_cache["__status__"] = "connected"
    print(f"[MOCK FEED] tick simulator starting — sid={sid}, {len(PRICE_WALK)} steps × {TICK_INTERVAL_S}s")

    def _run():
        for price in PRICE_WALK:
            if _stop.is_set():
                break
            tick = {
                "security_id": sid,
                "LTP":         float(price),
                "LTT":         datetime.now().strftime("%H:%M:%S"),
            }
            price_cache[sid] = tick
            if on_tick:
                try:
                    on_tick(sid, tick)
                except Exception as e:
                    print(f"[MOCK FEED] on_tick error: {e}")
            time.sleep(TICK_INTERVAL_S)
        print("[MOCK FEED] price walk complete")

    _sim_thread = threading.Thread(target=_run, daemon=True, name="mock-tick-sim")
    _sim_thread.start()


def stop_feed():
    _stop.set()
    price_cache["__status__"] = "disconnected"


def get_tick(security_id: str) -> dict | None:
    return price_cache.get(str(security_id))


def get_ltp(security_id: str) -> float | None:
    entry = price_cache.get(str(security_id))
    if not entry:
        return None
    return float(entry.get("LTP") or 0) or None


def feed_status() -> str:
    return price_cache.get("__status__", "disconnected")


def is_connected() -> bool:
    return feed_status() == "connected"


def reconnect_count() -> int:
    return 0


def last_error() -> str | None:
    return None
