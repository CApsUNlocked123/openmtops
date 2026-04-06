"""
Candle Service — background daemon that fetches 5-minute OHLCV candles
from Dhan's intraday_minute_data API and stores them in SQLite.

Runs independently of OI Tracker, Live trading, and all other features.
Dashboard reads from this DB; it never writes to it.

Market hours: Monday–Friday 09:15–15:30 IST
Fetch schedule: every 5-minute boundary + 35s buffer (e.g. 09:20:35, 09:25:35 …)
"""

import os
import sqlite3
import threading
import logging
from datetime import datetime, timedelta, time as dtime, date

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "candles.db")

TRACKED = [
    {"name": "NIFTY",      "security_id": "13",  "exchange": "IDX_I"},
    {"name": "BANKNIFTY",  "security_id": "25",  "exchange": "IDX_I"},
    {"name": "FINNIFTY",   "security_id": "27",  "exchange": "IDX_I"},
    {"name": "MIDCPNIFTY", "security_id": "442", "exchange": "IDX_I"},
]

INSTRUMENT_NAMES = [t["name"] for t in TRACKED]

# Market window (IST)
_MARKET_START = dtime(9, 14)    # start fetching slightly before 09:15
_MARKET_END   = dtime(15, 36)   # last possible 15:30 candle closes around 15:35

# ── Module-level state ────────────────────────────────────────────────────────

_stop_event = threading.Event()
_thread: threading.Thread | None = None


# ── DB setup ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS candles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument  TEXT    NOT NULL,
                security_id TEXT    NOT NULL,
                exchange    TEXT    NOT NULL,
                interval    INTEGER NOT NULL,
                time        TEXT    NOT NULL,
                open        REAL,
                high        REAL,
                low         REAL,
                close       REAL,
                volume      INTEGER,
                UNIQUE(instrument, interval, time)
            );
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
                ON candles(instrument, interval, time DESC);
        """)


# ── Time helpers ──────────────────────────────────────────────────────────────

def _is_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now.time()
    return _MARKET_START <= t <= _MARKET_END


def _floor_to_5min(now: datetime) -> datetime:
    """Floor datetime to the nearest past 5-minute boundary."""
    return now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)


def _seconds_to_next_fetch(now: datetime) -> float:
    """
    Seconds until the next fetch: next 5-min boundary + 35s buffer.
    Minimum 60 seconds to avoid hammering the API.
    """
    next_boundary = _floor_to_5min(now) + timedelta(minutes=5, seconds=35)
    if next_boundary <= now:
        next_boundary += timedelta(minutes=5)
    return max(60.0, (next_boundary - now).total_seconds())


# ── Fetch and store ───────────────────────────────────────────────────────────

def _epoch_to_ist_str(epoch: int) -> str:
    """Convert epoch seconds to 'YYYY-MM-DD HH:MM:SS' IST string."""
    # DhanHQ returns epoch in seconds (IST = UTC+5:30)
    from datetime import timezone
    IST_OFFSET = timedelta(hours=5, minutes=30)
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fetch_and_store_all():
    from dhan import dhan
    today = date.today().strftime("%Y-%m-%d")

    with _get_conn() as conn:
        for instr in TRACKED:
            name = instr["name"]
            sid  = instr["security_id"]
            exch = instr["exchange"]
            try:
                resp = dhan.intraday_minute_data(
                    sid, exch, "INDEX", today, today, interval=5
                )
                if resp.get("status") != "success":
                    log.warning("[candle_service] %s API error: %s", name, resp.get("remarks", resp))
                    continue

                data    = resp.get("data", {})
                opens   = data.get("open",       [])
                highs   = data.get("high",        [])
                lows    = data.get("low",         [])
                closes  = data.get("close",       [])
                volumes = data.get("volume",      [])
                times   = data.get("timestamp") or data.get("start_Time", [])

                if not times:
                    log.debug("[candle_service] %s — no candle data returned", name)
                    continue

                rows = []
                for i in range(len(times)):
                    t = times[i]
                    time_str = _epoch_to_ist_str(int(t)) if isinstance(t, (int, float)) else str(t)
                    rows.append((
                        name, sid, exch, 5, time_str,
                        float(opens[i])   if i < len(opens)   else None,
                        float(highs[i])   if i < len(highs)   else None,
                        float(lows[i])    if i < len(lows)    else None,
                        float(closes[i])  if i < len(closes)  else None,
                        int(volumes[i])   if i < len(volumes)  else 0,
                    ))

                conn.executemany("""
                    INSERT OR IGNORE INTO candles
                        (instrument, security_id, exchange, interval, time,
                         open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)

                # Trim to last 50 rows per instrument
                conn.execute("""
                    DELETE FROM candles
                    WHERE instrument = ? AND interval = 5
                      AND id NOT IN (
                          SELECT id FROM candles
                          WHERE instrument = ? AND interval = 5
                          ORDER BY time DESC LIMIT 50
                      )
                """, (name, name))

                log.info("[candle_service] %s — stored %d candles", name, len(rows))

            except Exception as e:
                log.error("[candle_service] %s fetch error: %s", name, e)


# ── Background loop ───────────────────────────────────────────────────────────

def _service_loop():
    log.info("[candle_service] started")
    # Do an immediate fetch if within market hours
    now = datetime.now()
    if _is_market_hours(now):
        _fetch_and_store_all()

    while not _stop_event.is_set():
        now     = datetime.now()
        timeout = _seconds_to_next_fetch(now)
        _stop_event.wait(timeout=timeout)

        if _stop_event.is_set():
            break

        now = datetime.now()
        if _is_market_hours(now):
            _fetch_and_store_all()
        else:
            log.debug("[candle_service] outside market hours, skipping fetch")

    log.info("[candle_service] stopped")


# ── Public API ────────────────────────────────────────────────────────────────

def start() -> None:
    """Start the background candle fetcher. Idempotent — safe to call multiple times."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _init_db()
    _thread = threading.Thread(target=_service_loop, name="candle-service", daemon=True)
    _thread.start()


def stop() -> None:
    """Signal the background thread to stop."""
    _stop_event.set()


def get_candles(instrument: str, n: int = 50) -> list[dict]:
    """
    Return the last `n` completed 5-minute candles for `instrument`,
    ordered oldest → newest (ascending time).
    Returns [] if the DB has no data yet.
    """
    instrument = instrument.upper()
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT time, open, high, low, close, volume
                FROM candles
                WHERE instrument = ? AND interval = 5
                ORDER BY time DESC
                LIMIT ?
            """, (instrument, n)).fetchall()
        # Reverse so oldest is first (ascending)
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        log.error("[candle_service] get_candles error: %s", e)
        return []


def get_db_path() -> str:
    return DB_PATH


def fetch_instrument(instrument: str) -> int:
    """
    On-demand fetch for a single instrument. Fetches today's 5-min candles
    from Dhan API and stores them in SQLite. Returns count of rows processed.
    Returns 0 outside market hours (API has no intraday data then).
    """
    if not _is_market_hours(datetime.now()):
        log.debug("[candle_service] fetch_instrument: outside market hours, skipping")
        return 0

    from dhan import dhan
    instrument = instrument.upper()
    instr_cfg = next((i for i in TRACKED if i["name"] == instrument), None)
    if not instr_cfg:
        log.warning("[candle_service] fetch_instrument: unknown instrument %s", instrument)
        return 0

    today = date.today().strftime("%Y-%m-%d")
    name  = instr_cfg["name"]
    sid   = instr_cfg["security_id"]
    exch  = instr_cfg["exchange"]

    try:
        resp = dhan.intraday_minute_data(sid, exch, "INDEX", today, today, interval=5)
        if resp.get("status") != "success":
            log.warning("[candle_service] fetch_instrument %s: %s", name, resp.get("remarks", resp))
            return 0

        data    = resp.get("data", {})
        opens   = data.get("open",       [])
        highs   = data.get("high",        [])
        lows    = data.get("low",         [])
        closes  = data.get("close",       [])
        volumes = data.get("volume",      [])
        times   = data.get("timestamp") or data.get("start_Time", [])

        if not times:
            return 0

        rows = []
        for i in range(len(times)):
            t = times[i]
            time_str = _epoch_to_ist_str(int(t)) if isinstance(t, (int, float)) else str(t)
            rows.append((
                name, sid, exch, 5, time_str,
                float(opens[i])   if i < len(opens)   else None,
                float(highs[i])   if i < len(highs)   else None,
                float(lows[i])    if i < len(lows)    else None,
                float(closes[i])  if i < len(closes)  else None,
                int(volumes[i])   if i < len(volumes)  else 0,
            ))

        with _get_conn() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO candles
                    (instrument, security_id, exchange, interval, time,
                     open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.execute("""
                DELETE FROM candles
                WHERE instrument = ? AND interval = 5
                  AND id NOT IN (
                      SELECT id FROM candles
                      WHERE instrument = ? AND interval = 5
                      ORDER BY time DESC LIMIT 50
                  )
            """, (name, name))

        log.info("[candle_service] fetch_instrument %s — %d candles", name, len(rows))
        return len(rows)

    except Exception as e:
        log.error("[candle_service] fetch_instrument %s error: %s", name, e)
        return 0


def get_live_candle(instrument: str) -> dict | None:
    """
    Fetch 1-min candles and aggregate the current (incomplete) 5-minute bar.
    Returns None outside market hours or if API has no data.
    The returned dict has a 'partial': True field.
    """
    from dhan import dhan
    instrument = instrument.upper()
    instr_cfg = next((i for i in TRACKED if i["name"] == instrument), None)
    if not instr_cfg:
        return None

    now = datetime.now()
    if not _is_market_hours(now):
        return None

    today = date.today().strftime("%Y-%m-%d")
    sid   = instr_cfg["security_id"]
    exch  = instr_cfg["exchange"]

    try:
        resp = dhan.intraday_minute_data(sid, exch, "INDEX", today, today, interval=1)
        if resp.get("status") != "success":
            return None

        data    = resp.get("data", {})
        opens   = data.get("open",       [])
        highs   = data.get("high",        [])
        lows    = data.get("low",         [])
        closes  = data.get("close",       [])
        volumes = data.get("volume",      [])
        times   = data.get("timestamp") or data.get("start_Time", [])

        if not times:
            return None

        # Collect 1-min bars that fall within the current 5-min bar window
        current_bar_start = _floor_to_5min(now)
        bar_opens = []; bar_highs = []; bar_lows = []; bar_closes = []; bar_vols = []

        for i in range(len(times)):
            t = times[i]
            time_str  = _epoch_to_ist_str(int(t)) if isinstance(t, (int, float)) else str(t)
            candle_dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            if candle_dt >= current_bar_start:
                if i < len(opens):   bar_opens.append(float(opens[i]))
                if i < len(highs):   bar_highs.append(float(highs[i]))
                if i < len(lows):    bar_lows.append(float(lows[i]))
                if i < len(closes):  bar_closes.append(float(closes[i]))
                if i < len(volumes): bar_vols.append(int(volumes[i]))

        if not bar_opens:
            return None

        mins_elapsed = int((now - current_bar_start).total_seconds() / 60)
        return {
            "time":           current_bar_start.strftime("%Y-%m-%d %H:%M:%S"),
            "open":           bar_opens[0],
            "high":           max(bar_highs),
            "low":            min(bar_lows),
            "close":          bar_closes[-1],
            "volume":         sum(bar_vols),
            "partial":        True,
            "minutes_elapsed": mins_elapsed,
        }

    except Exception as e:
        log.error("[candle_service] get_live_candle %s error: %s", instrument, e)
        return None
