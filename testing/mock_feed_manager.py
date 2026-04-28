"""
Mock feed manager helper for TESTING=1.

The real feed_manager is still used in testing (not mocked via sys.modules),
but inject_tick() lets tests drive the pov on_tick callback directly without
a live WebSocket.
"""


def inject_tick(sid: str, tick: dict) -> None:
    """
    Fire a synthetic tick into the named subscriber's on_tick callback.

    Example usage:
        from testing.mock_feed_manager import inject_tick
        inject_tick("123456", {"LTP": 245.5, "OI": 1500000, "LTQ": 50})
    """
    import feed_manager
    sub = feed_manager._subscribers.get("pov")
    if sub and callable(sub.get("on_tick")):
        sub["on_tick"](sid, tick)
