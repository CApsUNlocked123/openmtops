"""
Signal notifier — background thread that scans the strategy dashboard
indicators every 30 minutes during market hours and fires a notification
when a high-confidence ENTER signal is detected.

Call signal_notifier.start() from app.py after notification_service.start().
"""

# MODIFIED BY: OpenMTOps Spec v1.0
# CHANGES APPLIED: C12, C13, C14
# Each change is tagged inline with: # [CXX]

import threading
import logging
import time
from datetime import datetime, timezone, timedelta, time as dtime

from indicators_dashboard import check_whipsaw_lockout

log = logging.getLogger(__name__)

_COOLDOWN_SECONDS = 30 * 60   # 30-minute minimum between notifications per instrument
_last_notified: dict = {}      # instrument → epoch float

IST = timezone(timedelta(hours=5, minutes=30))

INSTRUMENTS = ["NIFTY", "BANKNIFTY"]


# ─────────────────────────────────────────────────────────────────────────────
# [C12] Blackout window + news-candle guard
# ─────────────────────────────────────────────────────────────────────────────

BLACKOUT_START_HOUR   = 9
BLACKOUT_START_MINUTE = 15
BLACKOUT_END_HOUR     = 9
BLACKOUT_END_MINUTE   = 30
MAX_SINGLE_BAR_MOVE   = 80    # points — any candle body > this = news, skip
WHIPSAW_LOCKOUT_MINS  = 45


def in_blackout(candle_time: dtime, candle_body_pts: float = 0.0) -> bool:
    """
    Return True if signal evaluation should be suppressed. [C12]

    Inputs:
        candle_time:     time of candle close (datetime.time).
        candle_body_pts: absolute point size of candle body (high - low).

    Outputs:
        True  — skip this candle, do not evaluate signal.
        False — proceed with evaluation.

    Edge case solved:
        Opening 15 minutes (9:15–9:30) are driven by gap fills and pre-market
        orders, not readable by any indicator. Single candles > 80pts indicate
        a news event where you cannot get a fair fill anyway.
    """
    blackout_start = dtime(BLACKOUT_START_HOUR, BLACKOUT_START_MINUTE)
    blackout_end   = dtime(BLACKOUT_END_HOUR,   BLACKOUT_END_MINUTE)

    in_window   = blackout_start <= candle_time <= blackout_end
    news_candle = candle_body_pts > MAX_SINGLE_BAR_MOVE

    return in_window or news_candle


# ─────────────────────────────────────────────────────────────────────────────
# [C13] SessionGuard — stateful per-day circuit breaker + whipsaw lockout
# ─────────────────────────────────────────────────────────────────────────────

class SessionGuard:
    """
    Stateful session-level guard. One instance per trading day. [C13]
    Tracks consecutive losses and whipsaw lockout.

    Stateful by design — the only stateful component in the system.
    Reset by calling reset_session() at market open (9:15 AM IST) each day.

    Edge case solved:
        Prevents the system from continuing to generate signals after two
        consecutive losses — no system handles all market conditions, the goal
        is to lose small, not win everything. Also manages the 45-minute
        whipsaw lockout window.
    """

    def __init__(self):
        self.consecutive_losses   = 0
        self.whipsaw_locked_until = None   # datetime or None
        self.regime_history       = []     # last N regimes for whipsaw detection

    def record_loss(self):
        """Call when a signal results in a loss (SL hit)."""
        self.consecutive_losses += 1

    def record_win(self):
        """Call when a signal results in a win (T1 or better hit)."""
        self.consecutive_losses = 0

    def record_regime(self, regime: str, current_dt):
        """Call every candle evaluation with the current regime."""
        self.regime_history.append(regime)
        if len(self.regime_history) > 10:
            self.regime_history = self.regime_history[-10:]

        if check_whipsaw_lockout(self.regime_history, current_dt.time()):
            self.whipsaw_locked_until = current_dt + timedelta(minutes=WHIPSAW_LOCKOUT_MINS)

    def is_circuit_broken(self) -> bool:
        """Return True if 2+ consecutive losses — stop trading for the day."""
        return self.consecutive_losses >= 2

    def is_whipsaw_locked(self, current_dt) -> bool:
        """Return True if still within whipsaw lockout window."""
        if self.whipsaw_locked_until is None:
            return False
        return current_dt < self.whipsaw_locked_until

    def can_trade(self, current_dt) -> tuple[bool, str]:
        """
        Master check. Returns (True, '') if trading is allowed,
        or (False, reason_string) if blocked.
        """
        if self.is_circuit_broken():
            return False, f"Circuit breaker: {self.consecutive_losses} consecutive losses today"
        if self.is_whipsaw_locked(current_dt):
            remaining = int((self.whipsaw_locked_until - current_dt).total_seconds() / 60)
            return False, f"Whipsaw lockout: {remaining} min remaining"
        return True, ""

    def reset_session(self):
        """Call at market open each day (9:15 AM IST)."""
        self.consecutive_losses   = 0
        self.whipsaw_locked_until = None
        self.regime_history       = []


# [C14] Module-level SessionGuard instance
_session_guard = SessionGuard()
_last_reset_date = None


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
    global _last_reset_date
    while True:
        if _is_market_hours():
            # [C14] Reset guard once per trading day at session start
            today = datetime.now(IST).date()
            if _last_reset_date != today:
                _session_guard.reset_session()
                _last_reset_date = today
                log.info("[signal_notifier] session guard reset for %s", today)

            for instrument in INSTRUMENTS:
                try:
                    _check_instrument(instrument)
                except Exception as e:
                    log.debug("[signal_notifier] %s error: %s", instrument, e)
        time.sleep(5 * 60)   # check every 5 minutes


def _check_instrument(instrument: str) -> None:
    from routes.dashboard import _build_snapshot

    snap = _build_snapshot(instrument)
    if not snap.get("ready"):
        return

    now_ist = datetime.now(IST)

    # [C14] Blackout check — uses the latest live/close candle body as news proxy
    live_candle = snap.get("live_candle") or {}
    if live_candle:
        body = abs(float(live_candle.get("high", 0)) - float(live_candle.get("low", 0)))
    else:
        body = 0.0
    if in_blackout(now_ist.time(), body):
        log.debug("[signal_notifier] blackout active — skipping %s", instrument)
        return

    # [C14] Record regime for whipsaw tracking
    regime = snap.get("regime", "")
    if regime:
        _session_guard.record_regime(regime, now_ist)

    # [C14] Session guard check
    can_trade, block_reason = _session_guard.can_trade(now_ist)
    if not can_trade:
        log.debug("[signal_notifier] SessionGuard blocked %s: %s", instrument, block_reason)
        return

    # Prefer structured final_signal from build_signal_output() if the
    # snapshot layer has been upgraded to provide it; fall back to the
    # legacy signals[] + score path otherwise.
    final_signal = snap.get("final_signal")
    if final_signal in ("ENTER_HIGH", "ENTER_LOW"):
        _maybe_notify(instrument, final_signal, snap, now_ist)
        return
    if final_signal in ("BLOCKED", "WAIT", "AVOID"):
        return

    # Legacy fallback
    signals = snap.get("signals") or []
    if not signals:
        return

    enter = next((s for s in signals if "ENTER" in s.get("action", "").upper()), None)
    if not enter:
        return

    score = enter.get("score", 0)
    if score < 70:
        return

    _maybe_notify(instrument, "ENTER_HIGH", snap, now_ist, legacy_score=score, legacy=True)


def _maybe_notify(instrument, final_signal, snap, now_ist, legacy_score=None, legacy=False):
    now_epoch = time.time()
    last = _last_notified.get(instrument, 0)
    if now_epoch - last < _COOLDOWN_SECONDS:
        return
    _last_notified[instrument] = now_epoch

    if legacy:
        regime = snap.get("regime", "")
        phase  = snap.get("phase", "")
        spot   = snap.get("spot", 0) or 0
        from notification_service import notify
        notify(
            title=f"ENTER Signal — {instrument}",
            body=f"Score {legacy_score}/100 · {regime} · {phase} · {spot:.0f}",
            category="signal",
            instrument=instrument,
            send_telegram=True,
        )
        return

    _send_enter_notification(instrument, {**snap, "final_signal": final_signal})


def _send_enter_notification(symbol: str, result: dict) -> None:
    """
    Format and send an ENTER notification using the full result dict
    from build_signal_output().
    """
    s    = result
    lvl  = s.get("dynamic_levels") or {}
    conf = s.get("confidence", {}) or {}
    lin  = s.get("linear_score", {}) or {}

    confidence_label = (
        "🟢 HIGH CONFIDENCE" if s.get("final_signal") == "ENTER_HIGH"
        else "🟡 LOW CONFIDENCE (reduce size)"
    )

    direction_label = "CE 📈" if s.get("signal_direction") == "LONG" else "PE 📉"

    msg_lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"SIGNAL: ENTER {direction_label}  {symbol}",
        f"{confidence_label}",
        f"Score:       {lin.get('score', '?')}/100",
        f"Confidence:  {conf.get('confidence_pct', '?')}%  "
        f"({conf.get('factors_agree', '?')}/{conf.get('total_factors', '?')} factors)",
        f"Day:         {s.get('day_character', '?')}",
        f"Phase:       {s.get('phase', '?')}",
        f"ATR:         {s.get('atr', '?')} pts",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if lvl:
        msg_lines += [
            f"Entry:  {lvl.get('t1', '?')}   (approx)",
            f"SL:     {lvl.get('sl', '?')}   (ATR × {lvl.get('atr_used', '?')})",
            f"T1:     {lvl.get('t1', '?')}   → move SL to breakeven",
            f"T2:     {lvl.get('t2', '?')}   → trail ATR after this",
        ]
        if lvl.get("t3"):
            msg_lines.append(f"T3:     {lvl['t3']}   (trail target)")
        msg_lines.append(f"R:R:    1 : {lvl.get('rr_t1', '?')}")

    if s.get("block_reason"):
        msg_lines.append(f"⚠️  {s['block_reason']}")

    msg_lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    body = "\n".join(msg_lines)

    from notification_service import notify
    notify(
        title=f"ENTER Signal — {symbol}",
        body=body,
        category="signal",
        instrument=symbol,
        send_telegram=True,
    )
