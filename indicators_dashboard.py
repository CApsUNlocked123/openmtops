"""
Strategy Dashboard Indicators — 7 pure functions.

All functions are stateless: no I/O, no globals, no side effects.
Inputs are plain Python dicts/lists produced by candle_service and the route.

Candle dict schema (from candle_service.get_candles):
    {"time": str, "open": float, "high": float, "low": float,
     "close": float, "volume": int}
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _body(c: dict) -> float:
    return abs(c["close"] - c["open"])

def _range(c: dict) -> float:
    r = c["high"] - c["low"]
    return r if r > 0 else 0.0001   # prevent division by zero


# ─────────────────────────────────────────────────────────────────────────────
# Function 1 — Regime Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_regime(candles: list[dict], ema_values: list[float | None]) -> str:
    """
    Classify the current market regime using last 12 five-minute candles + EMA9.

    Returns one of:
        IMPULSE_UP | IMPULSE_DOWN | REVERSAL_WATCH | CONSOLIDATION
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
    if len(candles) >= 2 and len(valid_emas) >= 2:
        c_prev, c_curr = candles[-2], candles[-1]
        e_prev, e_curr = valid_emas[-2], valid_emas[-1]
        crossed_up   = c_prev["close"] < e_prev and c_curr["close"] > e_curr
        crossed_down = c_prev["close"] > e_prev and c_curr["close"] < e_curr
        if crossed_up or crossed_down:
            return "REVERSAL_WATCH"

    if ema_slope > 3.0 and above_ema >= 3:
        return "IMPULSE_UP"
    if ema_slope < -3.0 and below_ema >= 3:
        return "IMPULSE_DOWN"
    return "CONSOLIDATION"


# ─────────────────────────────────────────────────────────────────────────────
# Function 2 — Move Velocity
# ─────────────────────────────────────────────────────────────────────────────

def compute_move_velocity(candles: list[dict]) -> dict:
    """
    Compute price velocity over the last 6 candles.

    Returns: {"velocity": float, "type": "SHARP"|"GRIND"|"FLAT"}
    """
    if len(candles) < 2:
        return {"velocity": 0.0, "type": "FLAT"}

    n = len(candles)
    velocity = abs(candles[-1]["close"] - candles[0]["close"]) / n
    velocity = round(velocity, 2)

    if velocity > 15:
        vtype = "SHARP"
    elif velocity >= 5:
        vtype = "GRIND"
    else:
        vtype = "FLAT"

    return {"velocity": velocity, "type": vtype}


# ─────────────────────────────────────────────────────────────────────────────
# Function 3 — Move Phase Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_move_phase(
    candles: list[dict],
    ema_values: list[float | None],
    oi_history: list[dict],
    regime: str,
) -> str:
    """
    Classify the current move phase using a strict waterfall.

    oi_history entries: {"total_ce_delta": int, "total_pe_delta": int}

    Returns one of:
        BASE | BREAKOUT | TREND_RIDE | EXHAUSTION | REVERSAL
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

    if score >= 70:
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
        """Extract HH:MM from a candle time string (handles both full and short formats)."""
        t = c.get("time", "")
        return t[11:16] if len(t) >= 16 else t[:5]

    result = []
    for entry in phase_log:
        phase      = entry.get("phase", "BASE")
        start_time = entry.get("start_time", "")
        end_time   = entry.get("end_time")

        # Compare only the HH:MM part of candle timestamps against HH:MM phase times
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
