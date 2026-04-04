"""
Mock candle_service module — replaces candle_service.py when TESTING=1.
Returns pre-built dummy NIFTY candles. No SQLite, no background fetch thread.
"""

from testing.dummy_data import CANDLES, LIVE_CANDLE

INSTRUMENT_NAMES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]


def get_candles(instrument: str, n: int = 50) -> list[dict]:
    return CANDLES[-n:]


def get_live_candle(instrument: str) -> dict | None:
    return dict(LIVE_CANDLE)


def fetch_instrument(instrument: str) -> int:
    return 0   # no-op


def start() -> None:
    print("[MOCK CANDLE SERVICE] started (no background thread)")
