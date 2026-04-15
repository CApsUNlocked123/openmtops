"""
Strategy Dashboard Indicators — pure functions.

All functions are stateless: no I/O, no globals, no side effects.
Inputs are plain Python dicts/lists produced by candle_service and the route.

Candle dict schema (from candle_service.get_candles):
    {"time": str, "open": float, "high": float, "low": float,
     "close": float, "volume": int}
"""

# MODIFIED BY: OpenMTOps Spec v1.0
# CHANGES APPLIED: C01, C02, C03, C04, C05, C06, C07, C08, C09, C10, C11
# Each change is tagged inline with: # [CXX]

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Constants — day character, ATR, thresholds, dynamic levels
# ─────────────────────────────────────────────────────────────────────────────

# [C01] Day character thresholds (directional/total range ratio)
DAY_TREND_RATIO_THRESHOLD = 0.65   # > this → TREND_DAY
DAY_RANGE_RATIO_THRESHOLD = 0.35   # > this → RANGE_DAY, else VOLATILE_DAY

# [C02] ATR
ATR_PERIOD = 14

# [C03] Late-entry guard
LATE_ENTRY_ATR_MULTIPLIER = 1.5   # move > 1.5x ATR in 3 candles = late

# [C04] Morning reversal filter cutoff
MORNING_REVERSAL_CUTOFF_HOUR   = 11
MORNING_REVERSAL_CUTOFF_MINUTE = 30

# [C05] Whipsaw lockout cutoff
WHIPSAW_LOCKOUT_BEFORE_HOUR   = 12
WHIPSAW_LOCKOUT_BEFORE_MINUTE = 0

# [C06] Exhaustion vs pause — min distance from EMA (as ATR multiple) to be a pause
EXHAUSTION_ATR_EMA_RATIO = 0.3

# [C07] Dynamic velocity thresholds (% of spot per bar)
VELOCITY_SHARP_PCT = 0.065
VELOCITY_GRIND_PCT = 0.022

# [C08] Confidence modifier thresholds
CONFIDENCE_HIGH_THRESHOLD = 75
CONFIDENCE_LOW_THRESHOLD  = 50

# [C09] ENTER score thresholds by day character
ENTER_THRESHOLD_TREND_DAY      = 65
ENTER_THRESHOLD_DEFAULT        = 70
ENTER_THRESHOLD_VOLATILE_DAY   = 85
ENTER_THRESHOLD_VOLATILE_RANGE = 150  # open-to-extreme pts by 10:30 → volatile

# [C11] ATR trailing stop
ATR_TRAIL_MULTIPLIER = 1.5

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _body(c: dict) -> float:
    return abs(c["close"] - c["open"])

def _range(c: dict) -> float:
    r = c["high"] - c["low"]
    return r if r > 0 else 0.0001   # prevent division by zero


# ─────────────────────────────────────────────────────────────────────────────
# Day character / ATR / late-entry utilities
# ─────────────────────────────────────────────────────────────────────────────

def classify_day_character(candles_first_6: list[dict]) -> str:
    """
    Classify the day's character after the first 6 five-minute candles (by 9:45 AM). [C01]

    Inputs:
        candles_first_6: list of 6 candle dicts with keys open, high, low, close.

    Outputs:
        'TREND_DAY'    — directional ratio > 0.65 (market has decided direction early)
        'RANGE_DAY'    — directional ratio 0.35–0.65 (balanced, wait for breakout)
        'VOLATILE_DAY' — directional ratio < 0.35 (chaotic, raise signal thresholds)

    Edge case solved:
        Prevents applying trend-day logic to news/volatile days (Apr 13 spike, Apr 2 V-shape).
        Called once at 9:45 AM and result passed to all downstream functions.
    """
    if len(candles_first_6) < 2:
        return "RANGE_DAY"

    directional = abs(candles_first_6[-1]["close"] - candles_first_6[0]["open"])
    total_high  = max(c["high"] for c in candles_first_6)
    total_low   = min(c["low"]  for c in candles_first_6)
    total_range = total_high - total_low

    if total_range == 0:
        return "RANGE_DAY"

    ratio = directional / total_range

    if ratio > DAY_TREND_RATIO_THRESHOLD:
        return "TREND_DAY"
    elif ratio > DAY_RANGE_RATIO_THRESHOLD:
        return "RANGE_DAY"
    else:
        return "VOLATILE_DAY"


def compute_atr(candles: list[dict], n: int = ATR_PERIOD) -> float:
    """
    Compute Average True Range over the last n candles. [C02]

    Inputs:
        candles: list of candle dicts with high, low, close keys.
        n:       lookback period (default 14).

    Outputs:
        float — ATR value in index points.

    Edge case solved:
        Provides a volatility-normalised base unit for all SL, target, and
        threshold calculations so they adapt to current market conditions.
    """
    if len(candles) < 2:
        return 50.0   # safe fallback in points

    true_ranges = []
    for i in range(1, len(candles)):
        high       = candles[i]["high"]
        low        = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high, prev_close) - min(low, prev_close)
        true_ranges.append(tr)

    period = min(n, len(true_ranges))
    return sum(true_ranges[-period:]) / period


def is_late_entry(candles: list[dict], atr: float) -> bool:
    """
    Detect if the current signal is arriving after the move has already happened. [C03]

    Inputs:
        candles: recent candle list. Needs at least 4 candles.
        atr:     current ATR from compute_atr().

    Outputs:
        True  — signal is LATE, suppress ENTER.
        False — signal timing is acceptable.

    Edge case solved:
        On Apr 13, Nifty spiked 400pts in the final 30 minutes. The system would
        fire ENTER on the next candle — after the entire move was done. This guard
        detects that the 3-candle move exceeds 1.5x ATR and blocks the late entry.

    Note: This fires REACTIVELY — one candle after the spike. It cannot prevent
    entry into the spike itself, only prevent chasing it afterward.
    """
    if len(candles) < 4 or atr <= 0:
        return False

    move = abs(candles[-1]["close"] - candles[-4]["close"])
    return move > LATE_ENTRY_ATR_MULTIPLIER * atr


# ─────────────────────────────────────────────────────────────────────────────
# Function 1 — Regime Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_regime(
    candles: list[dict],
    ema_values: list[float | None],
    session_time=None,   # [C04]
) -> str:
    """
    Classify the current market regime using last 12 five-minute candles + EMA9.

    Returns one of:
        IMPULSE_UP | IMPULSE_DOWN | REVERSAL_WATCH | CONSOLIDATION

    session_time (datetime.time, optional) — when provided, REVERSAL_WATCH
    before 11:30 AM IST is downgraded to CONSOLIDATION (morning reversal
    filter, [C04]).
    """
    if len(candles) < 4 or len(ema_values) < 4:
        return "CONSOLIDATION"

    # Filter out None EMA values at the tail
    valid_emas = [e for e in ema_values if e is not None]
    if len(valid_emas) < 4:
        return "CONSOLIDATION"

    # EMA slope: average pts/bar over the last 4 valid EMA readings
    ema_slope = (valid_emas[-1] - valid_emas[-4]) / 3.0

    # Count how many of the last 4 closes are above/below EMA9
    last4_c    = candles[-4:]
    last4_ema  = valid_emas[-4:]
    above_ema  = sum(1 for c, e in zip(last4_c, last4_ema) if c["close"] > e)
    below_ema  = sum(1 for c, e in zip(last4_c, last4_ema) if c["close"] < e)

    # Reversal watch: EMA cross in last 2 candles
    result = None
    if len(candles) >= 2 and len(valid_emas) >= 2:
        c_prev, c_curr = candles[-2], candles[-1]
        e_prev, e_curr = valid_emas[-2], valid_emas[-1]
        crossed_up   = c_prev["close"] < e_prev and c_curr["close"] > e_curr
        crossed_down = c_prev["close"] > e_prev and c_curr["close"] < e_curr
        if crossed_up or crossed_down:
            result = "REVERSAL_WATCH"

    if result is None:
        if ema_slope > 3.0 and above_ema >= 3:
            result = "IMPULSE_UP"
        elif ema_slope < -3.0 and below_ema >= 3:
            result = "IMPULSE_DOWN"
        else:
            result = "CONSOLIDATION"

    # [C04] Morning reversal filter: REVERSAL_WATCH before 11:30 AM is
    # statistically noise in Nifty — the index probes a direction at open
    # then reverses to find range. Downgrade to CONSOLIDATION.
    if session_time is not None and result == "REVERSAL_WATCH":
        cutoff = MORNING_REVERSAL_CUTOFF_HOUR * 60 + MORNING_REVERSAL_CUTOFF_MINUTE
        now    = session_time.hour * 60 + session_time.minute
        if now < cutoff:
            result = "CONSOLIDATION"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Whipsaw lockout — [C05]
# ─────────────────────────────────────────────────────────────────────────────

def check_whipsaw_lockout(regime_history: list[str], session_time) -> bool:
    """
    Detect if the market has whipsawed (regime flipped direction) before noon. [C05]

    Inputs:
        regime_history: list of regime strings in chronological order.
                        Only the last two entries are used.
        session_time:   datetime.time of current candle close.

    Outputs:
        True  — whipsaw detected, caller should block ENTER for 45 minutes.
        False — no whipsaw, or whipsaw happened after noon.

    Edge case solved:
        On Apr 2 (V-shape day), the system fires ENTER PE in morning and ENTER CE
        at 12:30 — two opposing signals in the same session. The lockout prevents
        the second opposing entry for 45 minutes after the regime flip.

    Note: Caller is responsible for tracking lockout duration. This function only
    detects the whipsaw event itself; the 45-minute window is managed in
    signal_notifier.py via SessionGuard.
    """
    if len(regime_history) < 2:
        return False

    prev = regime_history[-2]
    curr = regime_history[-1]

    opposing_flip = (
        (prev == "IMPULSE_UP"   and curr == "IMPULSE_DOWN") or
        (prev == "IMPULSE_DOWN" and curr == "IMPULSE_UP")
    )

    if not opposing_flip:
        return False

    cutoff = WHIPSAW_LOCKOUT_BEFORE_HOUR * 60 + WHIPSAW_LOCKOUT_BEFORE_MINUTE
    now    = session_time.hour * 60 + session_time.minute
    return now < cutoff


# ─────────────────────────────────────────────────────────────────────────────
# Function 2 — Move Velocity
# ─────────────────────────────────────────────────────────────────────────────

def compute_move_velocity(candles: list[dict], spot_price: float | None = None) -> dict:
    """
    Compute price velocity over the last N candles.

    If `spot_price` is provided, velocity is classified as a percentage of
    spot via classify_velocity_dynamic() [C07]. Otherwise the original
    hardcoded point thresholds apply.

    Returns: {"velocity": float, "type": "SHARP"|"GRIND"|"FLAT"}
    """
    if len(candles) < 2:
        return {"velocity": 0.0, "type": "FLAT"}

    n = len(candles)
    velocity = abs(candles[-1]["close"] - candles[0]["close"]) / n
    velocity = round(velocity, 2)

    if spot_price is not None and spot_price > 0:
        vtype = classify_velocity_dynamic(velocity, spot_price)
    elif velocity > 15:
        vtype = "SHARP"
    elif velocity >= 5:
        vtype = "GRIND"
    else:
        vtype = "FLAT"

    return {"velocity": velocity, "type": vtype}


def classify_velocity_dynamic(velocity_pts: float, spot_price: float) -> str:
    """
    Classify velocity as a percentage of the current spot price instead of
    using fixed point thresholds. [C07]

    Inputs:
        velocity_pts: absolute point move per bar (from compute_move_velocity).
        spot_price:   current index level (e.g. 23500.0).

    Outputs:
        'SHARP' | 'GRIND' | 'FLAT'

    Edge case solved:
        The original hardcoded thresholds (SHARP > 15pts, GRIND >= 5pts) become
        meaninglessly loose if Nifty trades at 25000 vs 15000. Percentage-based
        thresholds scale with the index level automatically.
    """
    if spot_price <= 0:
        return "FLAT"

    pct = (velocity_pts / spot_price) * 100.0

    if pct > VELOCITY_SHARP_PCT:
        return "SHARP"
    elif pct > VELOCITY_GRIND_PCT:
        return "GRIND"
    else:
        return "FLAT"


# ─────────────────────────────────────────────────────────────────────────────
# Function 3 — Move Phase Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_move_phase(
    candles: list[dict],
    ema_values: list[float | None],
    oi_history: list[dict],
    regime: str,
    atr: float = 0.0,   # [C06]
) -> str:
    """
    Classify the current move phase using a strict waterfall.

    oi_history entries: {"total_ce_delta": int, "total_pe_delta": int}

    Returns one of:
        BASE | BREAKOUT | TREND_RIDE | TREND_PAUSE | EXHAUSTION | REVERSAL

    TREND_PAUSE is returned when shrinking bodies + slowing OI would normally
    trigger EXHAUSTION, but price is still firmly beyond 0.3x ATR from the
    EMA — the trend is pausing, not exhausting. [C06]
    """
    if not candles:
        return "BASE"

    vel = compute_move_velocity(candles[-6:] if len(candles) >= 6 else candles)

    # REVERSAL — earliest exit
    if regime == "REVERSAL_WATCH":
        return "REVERSAL"

    # BASE — flat consolidation
    if regime == "CONSOLIDATION" and vel["type"] == "FLAT":
        return "BASE"

    # BREAKOUT — regime just flipped to IMPULSE from prior consolidation
    if regime in ("IMPULSE_UP", "IMPULSE_DOWN") and len(candles) >= 4:
        prior_3 = candles[-4:-1]
        was_consolidating = all(
            _body(c) / _range(c) < 0.40 for c in prior_3
        )
        if was_consolidating:
            return "BREAKOUT"

    # EXHAUSTION — before TREND_RIDE check so it takes priority when detected
    if regime in ("IMPULSE_UP", "IMPULSE_DOWN") and len(candles) >= 4:
        valid_emas = [e for e in ema_values if e is not None]
        last4_c   = candles[-4:]
        last4_ema = valid_emas[-4:] if len(valid_emas) >= 4 else []

        # 4+ closes on same side of EMA
        if last4_ema:
            if regime == "IMPULSE_UP":
                on_trend_side = sum(1 for c, e in zip(last4_c, last4_ema) if c["close"] > e)
            else:
                on_trend_side = sum(1 for c, e in zip(last4_c, last4_ema) if c["close"] < e)
        else:
            on_trend_side = 0

        if on_trend_side >= 3 and vel["type"] in ("GRIND", "SHARP"):
            # Check exhaustion: shrinking bodies over last 3 candles
            last3 = candles[-3:]
            bodies = [_body(c) for c in last3]
            shrinking = all(bodies[i] < bodies[i - 1] for i in range(1, len(bodies)))

            # OI delta slowing: last delta < 80% of prior average
            oi_slowing = False
            if oi_history and len(oi_history) >= 2:
                last_oi   = oi_history[-1]
                prior_oi  = oi_history[:-1]
                last_net  = abs(last_oi.get("total_pe_delta", 0) - last_oi.get("total_ce_delta", 0))
                prior_net = [abs(o.get("total_pe_delta", 0) - o.get("total_ce_delta", 0)) for o in prior_oi]
                prior_avg = sum(prior_net) / len(prior_net) if prior_net else 0
                oi_slowing = (last_net < prior_avg * 0.80) if prior_avg > 0 else False

            if shrinking and (oi_slowing or not oi_history):
                # [C06] Distinguish true EXHAUSTION from TREND_PAUSE.
                # If bodies are shrinking but price is still firmly beyond
                # 0.3x ATR from EMA, the trend is pausing, not exhausting.
                if atr > 0 and last4_ema:
                    dist_to_ema = abs(candles[-1]["close"] - last4_ema[-1])
                    if dist_to_ema > EXHAUSTION_ATR_EMA_RATIO * atr:
                        return "TREND_PAUSE"

                return "EXHAUSTION"

            return "TREND_RIDE"

    # CONSOLIDATION with movement (GRIND/SHARP) — still BASE
    return "BASE"


# ─────────────────────────────────────────────────────────────────────────────
# Function 4 — Trend Health Score
# ─────────────────────────────────────────────────────────────────────────────

def compute_trend_health(
    candles: list[dict],
    ema_values: list[float | None],
    call_oi_delta: int,
    put_oi_delta: int,
    pcr_series: list[float],
) -> dict:
    """
    Score trend health from 0–100 by deducting for each warning signal.

    Returns: {"score": int, "warnings": [str, ...]}
    """
    score    = 100
    warnings = []

    if len(candles) < 3:
        return {"score": score, "warnings": []}

    valid_emas = [e for e in ema_values if e is not None]

    # ── Deduction 1: New high but volume < 70% of prior 3-bar average ─────────
    all_vols = [c["volume"] for c in candles]
    if any(v > 0 for v in all_vols):
        last_close   = candles[-1]["close"]
        prior_high   = max(c["high"] for c in candles[-4:-1]) if len(candles) >= 4 else 0
        last_vol     = candles[-1]["volume"]
        prior_vols   = [c["volume"] for c in candles[-4:-1]] if len(candles) >= 4 else []
        prior_avg_v  = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        if last_close > prior_high and prior_avg_v > 0 and last_vol < prior_avg_v * 0.70:
            score -= 10
            warnings.append("Volume not confirming new high")

    # ── Deduction 2: Call OI rising faster than put OI unwinding (uptrend) ───
    if call_oi_delta > 0 and put_oi_delta < 0:
        if abs(call_oi_delta) > abs(put_oi_delta) * 1.3:
            score -= 15
            warnings.append("Call writing exceeds put covering — ceiling risk")

    # ── Deduction 3: PCR fell 3 consecutive readings ─────────────────────────
    if len(pcr_series) >= 3:
        last3_pcr = pcr_series[-3:]
        if all(last3_pcr[i] < last3_pcr[i - 1] for i in range(1, 3)):
            score -= 15
            warnings.append("PCR falling 3 bars — bearish pressure")

    # ── Deduction 4: Close below EMA despite higher close ─────────────────────
    if len(candles) >= 2 and len(valid_emas) >= 1:
        c_curr  = candles[-1]
        c_prev  = candles[-2]
        ema_now = valid_emas[-1]
        if c_curr["close"] > c_prev["close"] and c_curr["close"] < ema_now:
            score -= 20
            warnings.append("Close below EMA despite up-move — distribution signal")

    # ── Deduction 5: Velocity dropping 3 consecutive bars ─────────────────────
    if len(candles) >= 5:
        vels = []
        for i in range(-3, 0):
            pair = candles[i - 1: i + 1] if i > -len(candles) else candles[:2]
            vels.append(abs(candles[i]["close"] - candles[i - 1]["close"]))
        if len(vels) == 3 and all(vels[j] < vels[j - 1] for j in range(1, 3)):
            score -= 10
            warnings.append("Momentum decelerating")

    # ── Deduction 6: 2+ consecutive doji/inside bars ──────────────────────────
    if len(candles) >= 2:
        last2 = candles[-2:]
        doji_count = sum(1 for c in last2 if _body(c) / _range(c) < 0.25)
        # Count inside bars only in the last 2 candles (not the full 12-bar window)
        n = len(candles)
        inside_count = sum(
            1 for i in range(max(1, n - 2), n)
            if candles[i]["high"] <= candles[i - 1]["high"]
            and candles[i]["low"]  >= candles[i - 1]["low"]
        )
        if doji_count >= 2 or inside_count >= 2:
            score -= 10
            warnings.append("Consolidation / indecision pattern")

    score = max(0, min(100, score))
    return {"score": score, "warnings": warnings}


# ─────────────────────────────────────────────────────────────────────────────
# Confidence score — [C08]
# ─────────────────────────────────────────────────────────────────────────────

def compute_confidence_score(
    regime:           str,
    phase:            str,
    velocity_type:    str,
    pcr:              float,
    oi_direction:     float,
    ema_slope_15m:    float | None,
    volume_ratio:     float,
    session_time,
    signal_direction: str,
) -> dict:
    """
    Count how many independent factors agree with the proposed signal direction. [C08]
    Returns a confidence percentage and a recommendation modifier.

    Inputs:
        regime:           output of classify_regime()
        phase:            output of classify_move_phase()
        velocity_type:    'SHARP' | 'GRIND' | 'FLAT'
        pcr:              Put-Call Ratio (float, e.g. 1.15)
        oi_direction:     -100 to +100 (negative = call buildup, positive = put buildup/bullish)
        ema_slope_15m:    EMA slope from 15-min candles. Pass None if unavailable.
        volume_ratio:     last bar volume / 20-bar average
        session_time:     datetime.time of signal
        signal_direction: 'LONG' (CE trade) or 'SHORT' (PE trade)

    Outputs:
        {
          'confidence_pct':  int,          # 0-100
          'factors_agree':   int,          # number of agreeing factors
          'total_factors':   int,          # total factors checked
          'modifier':        str,          # 'HIGH' | 'LOW' | 'CONTRADICT'
          'detail':          list[str]     # which factors agreed / disagreed
        }

    Edge case solved:
        Prevents treating a score of 71 the same as a score of 95. This is the
        core probability layer — a high score with contradicting factors is
        converted to WAIT regardless.

    Confidence modifier effect on final signal:
        HIGH       (>= 75%) — ENTER at full position size
        LOW        (50-74%) — ENTER at reduced position size (caller decides sizing)
        CONTRADICT (< 50%)  — Override ENTER to WAIT regardless of score
    """
    agree    = []
    disagree = []
    is_long  = (signal_direction == "LONG")

    # Factor 1: Regime matches direction
    if is_long and regime == "IMPULSE_UP":
        agree.append("Regime: IMPULSE_UP ✓")
    elif not is_long and regime == "IMPULSE_DOWN":
        agree.append("Regime: IMPULSE_DOWN ✓")
    else:
        disagree.append(f"Regime: {regime} conflicts with {signal_direction}")

    # Factor 2: Phase is actionable
    if phase in ("BREAKOUT", "TREND_RIDE"):
        agree.append(f"Phase: {phase} ✓")
    else:
        disagree.append(f"Phase: {phase} not actionable")

    # Factor 3: Velocity is meaningful
    if velocity_type in ("SHARP", "GRIND"):
        agree.append(f"Velocity: {velocity_type} ✓")
    else:
        disagree.append("Velocity: FLAT — no momentum")

    # Factor 4: PCR supports direction
    if is_long and pcr >= 1.0:
        agree.append(f"PCR: {pcr:.2f} bullish ✓")
    elif not is_long and pcr < 0.9:
        agree.append(f"PCR: {pcr:.2f} bearish ✓")
    else:
        disagree.append(f"PCR: {pcr:.2f} does not confirm {signal_direction}")

    # Factor 5: OI direction agrees
    if is_long and oi_direction > 10:
        agree.append(f"OI direction: {oi_direction:.0f} bullish ✓")
    elif not is_long and oi_direction < -10:
        agree.append(f"OI direction: {oi_direction:.0f} bearish ✓")
    else:
        disagree.append(f"OI direction: {oi_direction:.0f} neutral/against")

    # Factor 6: 15-min EMA slope agrees (optional)
    if ema_slope_15m is not None:
        if is_long and ema_slope_15m > 0:
            agree.append(f"15m EMA slope: {ema_slope_15m:.2f} confirms up ✓")
        elif not is_long and ema_slope_15m < 0:
            agree.append(f"15m EMA slope: {ema_slope_15m:.2f} confirms down ✓")
        else:
            disagree.append(f"15m EMA slope: {ema_slope_15m:.2f} conflicts")

    # Factor 7: Volume above average
    if volume_ratio >= 1.0:
        agree.append(f"Volume ratio: {volume_ratio:.2f}x ✓")
    else:
        disagree.append(f"Volume ratio: {volume_ratio:.2f}x below average")

    # Factor 8: Time past opening noise window
    if session_time is not None:
        session_minutes = session_time.hour * 60 + session_time.minute
        if session_minutes >= (9 * 60 + 45):
            agree.append("Time: past 9:45 AM ✓")
        else:
            disagree.append("Time: still in opening noise window")

    total = len(agree) + len(disagree)
    pct   = int((len(agree) / total) * 100) if total > 0 else 0

    if pct >= CONFIDENCE_HIGH_THRESHOLD:
        modifier = "HIGH"
    elif pct >= CONFIDENCE_LOW_THRESHOLD:
        modifier = "LOW"
    else:
        modifier = "CONTRADICT"

    return {
        "confidence_pct": pct,
        "factors_agree":  len(agree),
        "total_factors":  total,
        "modifier":       modifier,
        "detail":         agree + disagree,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENTER threshold helper — [C09]
# ─────────────────────────────────────────────────────────────────────────────

def get_enter_threshold(day_character: str, range_by_1030: float = 0.0) -> int:
    """
    Return the ENTER score threshold appropriate for the current day character. [C09]

    Inputs:
        day_character: output of classify_day_character() — 'TREND_DAY' |
                       'RANGE_DAY' | 'VOLATILE_DAY'
        range_by_1030: open-to-extreme point range by 10:30 AM. Optional.

    Outputs:
        int — minimum score required to generate an ENTER signal.

    Edge case solved:
        On V-shape day (Apr 2) and spike day (Apr 13), day_character = VOLATILE_DAY
        and threshold becomes 85, filtering out weak ENTER signals that look valid
        by the standard 70-threshold but occur in an unpredictable environment.
    """
    if day_character == "VOLATILE_DAY" or range_by_1030 > ENTER_THRESHOLD_VOLATILE_RANGE:
        return ENTER_THRESHOLD_VOLATILE_DAY
    elif day_character == "TREND_DAY":
        return ENTER_THRESHOLD_TREND_DAY
    else:
        return ENTER_THRESHOLD_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
# Function 5 — Linear Move Score
# ─────────────────────────────────────────────────────────────────────────────

def compute_linear_move_score(
    regime: str,
    velocity: dict,
    pcr: float,
    iv_percentile: float,
    oi_direction: float,
    volume_ratio: float,
    candle_structure_score: float,
    day_character: str = "RANGE_DAY",   # [C09]
    range_by_1030: float = 0.0,          # [C09]
) -> dict:
    """
    Weighted composite move score (0–100).

    oi_direction  : -100 (heavy call buildup) to +100 (heavy put buildup / bullish)
    iv_percentile : 0–100 (low = good for buyers)
    volume_ratio  : last-bar volume / 20-bar average

    Returns: {"score": int, "signal": str, "breakdown": dict}
    """
    # ── Per-component scores (0–100) ─────────────────────────────────────────
    vtype = velocity.get("type", "FLAT")
    ema_slope_score = {"SHARP": 100, "GRIND": 60, "FLAT": 20}.get(vtype, 20)

    # Normalize oi_direction -100..+100 → 0..100
    oi_buildup_score = min(100, max(0, (oi_direction + 100) / 2))

    # PCR trend: 1.3+ is bullish (score 80+), 0.7- is bearish (score 20-)
    if pcr >= 1.3:
        pcr_score = 80
    elif pcr >= 1.0:
        pcr_score = 60
    elif pcr >= 0.7:
        pcr_score = 40
    else:
        pcr_score = 20

    # IV direction: low IV (< 30) is good for buyers; high IV (> 70) favours sellers
    iv_score = max(0, min(100, 100 - iv_percentile))

    # Candle structure: passed in directly
    struct_score = max(0, min(100, candle_structure_score))

    # Volume ratio clamped 0.5→0, 2.0→100
    if volume_ratio <= 0.5:
        vol_score = 0.0
    elif volume_ratio >= 2.0:
        vol_score = 100.0
    else:
        vol_score = (volume_ratio - 0.5) / 1.5 * 100.0

    # ── Weighted sum ──────────────────────────────────────────────────────────
    weights = {
        "ema_slope":       0.20,
        "oi_buildup":      0.25,
        "pcr_trend":       0.15,
        "iv_direction":    0.15,
        "candle_structure": 0.15,
        "volume_ratio":    0.10,
    }
    component_vals = {
        "ema_slope":       ema_slope_score,
        "oi_buildup":      oi_buildup_score,
        "pcr_trend":       pcr_score,
        "iv_direction":    iv_score,
        "candle_structure": struct_score,
        "volume_ratio":    vol_score,
    }

    score = sum(component_vals[k] * weights[k] for k in weights)
    score = round(score)

    enter_threshold = get_enter_threshold(day_character, range_by_1030)   # [C09]
    if score >= enter_threshold:
        signal = "ENTER"
    elif score >= 40:
        signal = "WAIT"
    else:
        signal = "AVOID"

    breakdown = {k: round(v) for k, v in component_vals.items()}
    return {"score": score, "signal": signal, "breakdown": breakdown}


# ─────────────────────────────────────────────────────────────────────────────
# Function 6 — OI Wall Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_oi_wall(strike_oi_map: dict, current_price: float) -> dict:
    """
    Find the strongest support (put OI) and resistance (call OI) walls.

    strike_oi_map: {strike_int: {"ce_oi": int, "pe_oi": int}}

    Returns:
        {resistance_strike, resistance_oi, support_strike, support_oi,
         nearest_wall_distance}
    """
    if not strike_oi_map or not current_price:
        return {
            "resistance_strike": None, "resistance_oi": 0,
            "support_strike": None,    "support_oi": 0,
            "nearest_wall_distance": None,
        }

    above = {s: v["ce_oi"] for s, v in strike_oi_map.items() if s > current_price}
    below = {s: v["pe_oi"] for s, v in strike_oi_map.items() if s < current_price}

    resistance_strike = max(above, key=above.get) if above else None
    support_strike    = max(below, key=below.get) if below else None

    resistance_oi = above.get(resistance_strike, 0) if resistance_strike else 0
    support_oi    = below.get(support_strike, 0)    if support_strike    else 0

    distances = []
    if resistance_strike:
        distances.append(abs(resistance_strike - current_price))
    if support_strike:
        distances.append(abs(current_price - support_strike))
    nearest = round(min(distances), 2) if distances else None

    return {
        "resistance_strike":    resistance_strike,
        "resistance_oi":        resistance_oi,
        "support_strike":       support_strike,
        "support_oi":           support_oi,
        "nearest_wall_distance": nearest,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Function 7 — Phase Timeline Builder
# ─────────────────────────────────────────────────────────────────────────────

_PHASE_COLORS = {
    "BASE":       "#6c757d",
    "BREAKOUT":   "#ffc107",
    "EXHAUSTION": "#fd7e14",
    "REVERSAL":   "#0dcaf0",
}
_TREND_RIDE_UP   = "#198754"
_TREND_RIDE_DOWN = "#dc3545"


def build_phase_timeline(phase_log: list[dict], candles: list[dict]) -> list[dict]:
    """
    Build a list of timeline blocks for frontend rendering.

    phase_log entries:
        {"phase": str, "start_time": str, "end_time": str | None}

    Returns list of:
        {"start_time": str, "end_time": str|None, "phase": str,
         "color": str, "points_moved": float}
    """
    if not phase_log or not candles:
        return []

    def _hhmm(c: dict) -> str:
        """Extract HH:MM from a candle's full datetime string."""
        t = c.get("time", "")
        return t[11:16] if len(t) >= 16 else t[:5]

    result = []
    for entry in phase_log:
        phase      = entry.get("phase", "BASE")
        start_time = entry.get("start_time", "")
        end_time   = entry.get("end_time")

        last_hhmm = _hhmm(candles[-1])
        window = [
            c for c in candles
            if start_time <= _hhmm(c) <= (end_time or last_hhmm)
        ]

        if window:
            pts_moved = round(window[-1]["close"] - window[0]["open"], 2)
        else:
            pts_moved = 0.0

        if phase == "TREND_RIDE":
            color = _TREND_RIDE_UP if pts_moved >= 0 else _TREND_RIDE_DOWN
        else:
            color = _PHASE_COLORS.get(phase, "#6c757d")

        result.append({
            "start_time":   start_time,
            "end_time":     end_time,
            "phase":        phase,
            "color":        color,
            "points_moved": pts_moved,
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic levels — [C10] [C11]
# ─────────────────────────────────────────────────────────────────────────────

# [C10] ATR multipliers by phase and day character
# Format: (sl_mult, t1_mult, t2_mult, t3_mult_or_None, min_rr)
_LEVEL_MULTIPLIERS = {
    ("BREAKOUT",    "TREND_DAY"):    (1.0, 1.5, 3.0, 5.0, 1.5),
    ("BREAKOUT",    "RANGE_DAY"):    (0.8, 1.2, 2.0, None, 1.5),
    ("BREAKOUT",    "VOLATILE_DAY"): (1.5, 2.0, 3.5, None, 1.3),
    ("TREND_RIDE",  "TREND_DAY"):    (1.0, 2.0, 4.0, 6.0, 2.0),
    ("TREND_RIDE",  "RANGE_DAY"):    (1.0, 1.5, 2.5, None, 1.5),
    ("TREND_RIDE",  "VOLATILE_DAY"): (1.8, 2.5, 4.0, None, 1.4),
    ("TREND_PAUSE", "TREND_DAY"):    (1.0, 2.0, 4.0, 6.0, 2.0),
    ("TREND_PAUSE", "RANGE_DAY"):    (1.0, 1.5, 2.5, None, 1.5),
    ("TREND_PAUSE", "VOLATILE_DAY"): (1.8, 2.5, 4.0, None, 1.4),
    ("EXHAUSTION",  "TREND_DAY"):    (0.5, 0.8, 1.2, None, 1.6),
    ("EXHAUSTION",  "RANGE_DAY"):    (0.5, 0.8, 1.2, None, 1.6),
    ("EXHAUSTION",  "VOLATILE_DAY"): (0.5, 0.8, 1.2, None, 1.6),
}
_LEVEL_DEFAULT = (1.0, 1.5, 2.5, None, 1.5)


def compute_dynamic_levels(
    entry_price:   float,
    atr:           float,
    phase:         str,
    day_character: str,
    direction:     str,
) -> dict:
    """
    Compute ATR-based dynamic stop-loss and targets. [C10]

    Inputs:
        entry_price:   candle close at entry (index points).
        atr:           current ATR from compute_atr().
        phase:         current move phase ('BREAKOUT', 'TREND_RIDE', etc).
        day_character: 'TREND_DAY' | 'RANGE_DAY' | 'VOLATILE_DAY'.
        direction:     'LONG' (CE trade) | 'SHORT' (PE trade).

    Outputs:
        {
          'sl':       float,         # stop-loss level
          't1':       float,         # first target
          't2':       float,         # second target
          't3':       float | None,  # third target (TREND_DAY only)
          'rr_t1':    float,         # reward:risk ratio at T1
          'rr_valid': bool,          # True if R:R meets minimum for phase
          'trail_after_t2': bool,    # True if ATR trailing stop should
                                     # replace fixed target after T2 hit
          'atr_used': float,
        }

    Trail logic (caller implements):
        - On T1 hit: move SL to breakeven (entry_price)
        - On T2 hit: move SL to T1. If trail_after_t2=True, switch to
          ATR trailing stop (compute_atr_trailing_stop) instead of T3.

    Edge case solved:
        Fixed SL/target levels ignore volatility. On Apr 17 clean downtrend,
        ATR-based levels keep the trader in the 700pt move instead of
        exiting at a 50pt fixed target.
    """
    sl_m, t1_m, t2_m, t3_m, min_rr = _LEVEL_MULTIPLIERS.get(
        (phase, day_character), _LEVEL_DEFAULT
    )

    sign = 1.0 if direction == "LONG" else -1.0

    sl = round(entry_price - sign * sl_m * atr, 2)
    t1 = round(entry_price + sign * t1_m * atr, 2)
    t2 = round(entry_price + sign * t2_m * atr, 2)
    t3 = round(entry_price + sign * t3_m * atr, 2) if t3_m else None

    sl_dist = abs(entry_price - sl)
    t1_dist = abs(t1 - entry_price)
    rr_t1   = round(t1_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    return {
        "sl":             sl,
        "t1":             t1,
        "t2":             t2,
        "t3":             t3,
        "rr_t1":          rr_t1,
        "rr_valid":       rr_t1 >= min_rr,
        "trail_after_t2": t3_m is not None and day_character == "TREND_DAY",
        "atr_used":       round(atr, 2),
    }


def compute_atr_trailing_stop(
    candles:    list[dict],
    direction:  str,
    n:          int = ATR_PERIOD,
    multiplier: float = ATR_TRAIL_MULTIPLIER,
) -> dict:
    """
    Compute a trailing stop level based on current ATR. [C11]
    Called every candle once price has passed T2 on TREND_DAY entries.

    Inputs:
        candles:    recent candles. Minimum 2 needed.
        direction:  'LONG' | 'SHORT'
        n:          ATR period (default 14)
        multiplier: ATR multiplier for stop distance (default 1.5)

    Outputs:
        {
          'stop_level': float,   # absolute price of trailing stop
          'atr':        float,   # ATR value used
        }

    Usage:
        After T2 is hit, call this every candle close.
        If candles[-1]['close'] crosses stop_level, EXIT.
    """
    atr  = compute_atr(candles, n)
    last = candles[-1]["close"]

    if direction == "LONG":
        stop = round(last - multiplier * atr, 2)
    else:
        stop = round(last + multiplier * atr, 2)

    return {"stop_level": stop, "atr": round(atr, 2)}


# ─────────────────────────────────────────────────────────────────────────────
# Master pipeline — build_signal_output
# ─────────────────────────────────────────────────────────────────────────────

def build_signal_output(
    candles:         list[dict],
    candles_15m:     list[dict] | None,
    ema_values:      list[float | None],
    ema_values_15m:  list[float | None] | None,
    oi_history:      list[dict],
    strike_oi_map:   dict,
    pcr:             float,
    iv_percentile:   float,
    call_oi_delta:   int,
    put_oi_delta:    int,
    pcr_series:      list[float],
    volume_ratio:    float,
    session_time,
    candles_first_6: list[dict] | None = None,
    range_by_1030:   float = 0.0,
    regime_history:  list[str] | None = None,
) -> dict:
    """
    Master pipeline: runs all indicator functions and returns a unified
    signal output dict suitable for the dashboard route and signal_notifier.

    Returns:
    {
      'day_character':    str,
      'regime':           str,
      'phase':            str,
      'velocity':         dict,
      'trend_health':     dict,
      'linear_score':     dict,
      'confidence':       dict,
      'oi_walls':         dict,
      'atr':              float,
      'dynamic_levels':   dict | None,
      'whipsaw_lockout':  bool,
      'late_entry':       bool,
      'final_signal':     str,   # ENTER_HIGH | ENTER_LOW | WAIT | AVOID | BLOCKED
      'signal_direction': str | None,
      'block_reason':     str | None,
    }
    """
    # ── Step 1: Day character ───────────────────────────────────────────────
    first_6 = candles_first_6 if candles_first_6 else candles[:6]
    day_char = classify_day_character(first_6)

    # ── Step 2: ATR ─────────────────────────────────────────────────────────
    atr = compute_atr(candles)

    # ── Step 3: Regime + velocity ───────────────────────────────────────────
    regime   = classify_regime(candles, ema_values, session_time)
    velocity = compute_move_velocity(
        candles[-6:] if len(candles) >= 6 else candles,
        spot_price=candles[-1]["close"] if candles else None,
    )

    # ── Step 4: Phase ───────────────────────────────────────────────────────
    phase = classify_move_phase(candles, ema_values, oi_history, regime, atr=atr)

    # ── Step 5: Trend health ────────────────────────────────────────────────
    trend_health = compute_trend_health(
        candles, ema_values, call_oi_delta, put_oi_delta, pcr_series
    )

    # ── Step 6: OI walls ────────────────────────────────────────────────────
    current_price = candles[-1]["close"] if candles else 0
    oi_walls = detect_oi_wall(strike_oi_map, current_price)

    # ── Step 7: OI direction scalar ─────────────────────────────────────────
    ce_abs = abs(call_oi_delta) if call_oi_delta else 0
    pe_abs = abs(put_oi_delta)  if put_oi_delta  else 0
    total_oi = ce_abs + pe_abs
    if total_oi > 0:
        oi_direction = ((pe_abs - ce_abs) / total_oi) * 100.0
    else:
        oi_direction = 0.0

    # ── Step 8: 15-min EMA slope ────────────────────────────────────────────
    ema_slope_15m = None
    if ema_values_15m:
        valid_15m = [e for e in ema_values_15m if e is not None]
        if len(valid_15m) >= 4:
            ema_slope_15m = (valid_15m[-1] - valid_15m[-4]) / 3.0

    # ── Step 9: Linear score ────────────────────────────────────────────────
    cs_score = 100 - (len(trend_health.get("warnings", [])) * 15)
    linear = compute_linear_move_score(
        regime=regime,
        velocity=velocity,
        pcr=pcr,
        iv_percentile=iv_percentile,
        oi_direction=oi_direction,
        volume_ratio=volume_ratio,
        candle_structure_score=cs_score,
        day_character=day_char,
        range_by_1030=range_by_1030,
    )

    # ── Step 10: Determine signal direction ─────────────────────────────────
    if regime == "IMPULSE_UP":
        sig_dir = "LONG"
    elif regime == "IMPULSE_DOWN":
        sig_dir = "SHORT"
    else:
        sig_dir = None

    # ── Step 11: Confidence ─────────────────────────────────────────────────
    confidence = compute_confidence_score(
        regime=regime,
        phase=phase,
        velocity_type=velocity["type"],
        pcr=pcr,
        oi_direction=oi_direction,
        ema_slope_15m=ema_slope_15m,
        volume_ratio=volume_ratio,
        session_time=session_time,
        signal_direction=sig_dir or "LONG",
    )

    # ── Step 12: Block checks ───────────────────────────────────────────────
    block_reason = None

    late = is_late_entry(candles, atr) if len(candles) >= 4 else False
    if late:
        block_reason = "LATE_ENTRY: price moved >1.5x ATR in last 3 candles"

    wl = False
    if regime_history and session_time:
        wl = check_whipsaw_lockout(regime_history, session_time)
    if wl and not block_reason:
        block_reason = "WHIPSAW_LOCKOUT: opposing regime flip before noon"

    if (linear["signal"] == "ENTER"
            and confidence["modifier"] == "CONTRADICT"
            and not block_reason):
        block_reason = (
            f"CONTRADICT: score={linear['score']} but "
            f"confidence={confidence['confidence_pct']}% — factors disagree"
        )

    # ── Step 13: Final signal ───────────────────────────────────────────────
    if block_reason:
        final_signal = "BLOCKED"
    elif linear["signal"] == "ENTER":
        if confidence["modifier"] == "HIGH":
            final_signal = "ENTER_HIGH"
        elif confidence["modifier"] == "LOW":
            final_signal = "ENTER_LOW"
        else:
            final_signal = "WAIT"
    elif linear["signal"] == "WAIT":
        final_signal = "WAIT"
    else:
        final_signal = "AVOID"

    # ── Step 14: Dynamic levels (only if entering) ──────────────────────────
    dynamic_levels = None
    if final_signal in ("ENTER_HIGH", "ENTER_LOW") and sig_dir:
        levels = compute_dynamic_levels(
            entry_price=current_price,
            atr=atr,
            phase=phase,
            day_character=day_char,
            direction=sig_dir,
        )
        if not levels["rr_valid"]:
            final_signal = "WAIT"
            block_reason = (
                f"R:R invalid: {levels['rr_t1']} < minimum for {phase}/{day_char}"
            )
        else:
            dynamic_levels = levels

    return {
        "day_character":    day_char,
        "regime":           regime,
        "phase":            phase,
        "velocity":         velocity,
        "trend_health":     trend_health,
        "linear_score":     linear,
        "confidence":       confidence,
        "oi_walls":         oi_walls,
        "atr":              round(atr, 2),
        "dynamic_levels":   dynamic_levels,
        "whipsaw_lockout":  wl,
        "late_entry":       late,
        "final_signal":     final_signal,
        "signal_direction": sig_dir,
        "block_reason":     block_reason,
    }
