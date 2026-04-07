"""
Signal Engine — generates option buy / no-trade signals from dashboard KPIs.

Stateless computation + per-instrument state tracking for change detection.
Called from routes/dashboard.py on every 5s snapshot.

Signal lifecycle:
  BUY      — all conditions aligned, entry/target/SL available
  NO_TRADE — one or more counter signals active (exit or stay out)
  WAIT     — conditions improving but not yet confirmed

is_new = True only on state change or BUY cooldown reset (15 min).
"""

from __future__ import annotations
from datetime import datetime

# ── Per-instrument signal state (change detection + cooldown) ─────────────────
_signal_state: dict[str, dict] = {}

# ── Tuning constants ──────────────────────────────────────────────────────────
_BUY_PHASE_TRIGGERS  = {"BREAKOUT", "TREND_RIDE"}
_EXIT_PHASE_TRIGGERS = {"EXHAUSTION", "REVERSAL"}

_MIN_LINEAR_SCORE  = 65    # below → WAIT
_MIN_HEALTH_SCORE  = 50    # below → WAIT
_NO_TRADE_HEALTH   = 40    # below → NO_TRADE
_NO_TRADE_LINEAR   = 40    # below → NO_TRADE

_COOLDOWN_MINUTES  = 15    # repeat BUY cooldown

# 2:1 R:R  — target +12.5%, SL −6.25%
_TARGET_MULT = 1.125
_SL_MULT     = 0.9375


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_signal(
    instrument:   str,
    regime:       str,
    phase:        str,
    health:       dict,
    linear_score: dict,
    velocity:     dict,
    oi_snap:      dict | None,
    spot:         float | None,
) -> dict:
    """
    Returns a signal dict. Always safe to call; never raises.

    Keys always present:
        action          "BUY" | "NO_TRADE" | "WAIT"
        direction       "CE"  | "PE"  | None
        instrument      str
        atm_strike      int   | None
        entry           float | None
        target          float | None
        sl              float | None
        reason          str   (primary human-readable reason)
        counter_reasons [str] (list; non-empty only when action == NO_TRADE)
        regime          str
        phase           str
        health_score    int
        lin_score       int
        generated_at    "HH:MM:SS"
        is_new          bool  (True only on state transition or BUY cooldown reset)
    """
    now = datetime.now()

    health_score = (health or {}).get("score", 0)
    lin_score    = (linear_score or {}).get("score", 0)
    vel_type     = (velocity or {}).get("type", "FLAT")

    # ── Step 1: collect counter signals ───────────────────────────────────────
    counter = _collect_counter_reasons(regime, phase, health_score, lin_score, vel_type)

    if counter:
        return _dedup(instrument, _make(
            action="NO_TRADE", direction=None, instrument=instrument,
            atm_strike=None, entry=None, target=None, sl=None,
            reason="Counter signals active — stay out or exit open position",
            counter_reasons=counter,
            regime=regime, phase=phase,
            health_score=health_score, lin_score=lin_score, now=now,
        ))

    # ── Step 2: buy conditions ─────────────────────────────────────────────────
    if (regime in ("IMPULSE_UP", "IMPULSE_DOWN")
            and phase in _BUY_PHASE_TRIGGERS
            and lin_score >= _MIN_LINEAR_SCORE
            and health_score >= _MIN_HEALTH_SCORE
            and vel_type != "FLAT"):

        direction = "CE" if regime == "IMPULSE_UP" else "PE"
        entry, atm_strike = _get_entry(direction, oi_snap)

        if entry:
            target = _round_premium(entry * _TARGET_MULT)
            sl     = _round_premium(entry * _SL_MULT)
            reason = (
                f"{'Bullish' if direction == 'CE' else 'Bearish'} setup — "
                f"{phase}, {regime}, score {lin_score}/100, health {health_score}/100"
            )
            return _dedup(instrument, _make(
                action="BUY", direction=direction, instrument=instrument,
                atm_strike=atm_strike, entry=entry, target=target, sl=sl,
                reason=reason, counter_reasons=[],
                regime=regime, phase=phase,
                health_score=health_score, lin_score=lin_score, now=now,
            ))
        else:
            # Conditions met but OI LTP unavailable
            return _dedup(instrument, _make(
                action="WAIT", direction=direction, instrument=instrument,
                atm_strike=None, entry=None, target=None, sl=None,
                reason=f"Buy conditions met ({direction}) but OI tracker not running — start OI tracking for entry price",
                counter_reasons=[],
                regime=regime, phase=phase,
                health_score=health_score, lin_score=lin_score, now=now,
            ))

    # ── Step 3: WAIT ──────────────────────────────────────────────────────────
    reason = _wait_reason(regime, phase, lin_score, health_score, vel_type)
    return _dedup(instrument, _make(
        action="WAIT", direction=None, instrument=instrument,
        atm_strike=None, entry=None, target=None, sl=None,
        reason=reason, counter_reasons=[],
        regime=regime, phase=phase,
        health_score=health_score, lin_score=lin_score, now=now,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _collect_counter_reasons(
    regime: str, phase: str, health_score: int, lin_score: int, vel_type: str
) -> list[str]:
    """Return non-empty list if any counter signal is active."""
    reasons = []
    if phase in _EXIT_PHASE_TRIGGERS:
        reasons.append(f"Phase is {phase} — momentum fading, consider exit")
    if regime == "REVERSAL_WATCH":
        reasons.append("EMA crossover detected — reversal in progress")
    if regime == "CONSOLIDATION" and phase not in ("BREAKOUT",):
        reasons.append("Market in consolidation — no directional edge")
    if health_score < _NO_TRADE_HEALTH:
        reasons.append(f"Trend health weak ({health_score}/100) — structure breaking down")
    if lin_score < _NO_TRADE_LINEAR:
        reasons.append(f"Linear score low ({lin_score}/100) — avoid new entries")
    if vel_type == "FLAT" and regime in ("IMPULSE_UP", "IMPULSE_DOWN"):
        reasons.append("Velocity flat despite impulse regime — momentum stalling")
    return reasons


def _get_entry(direction: str, oi_snap: dict | None) -> tuple[float | None, int | None]:
    """
    Return (entry_price, strike) for 1 strike ITM from the live spot.

    Why ITM and not ATM:
      - ATM stored in oi_snap is fixed at tracking-start time.
        If spot has moved since, that "ATM" is now stale OTM.
      - Using live ultp (put-call parity, updated each tick) gives
        the true current ATM, then we step 1 strike ITM for a
        meaningful premium with decent delta.

    CE ITM: one strike BELOW live spot  (strike < spot)
    PE ITM: one strike ABOVE live spot  (strike > spot)
    Falls back to stored atm_strike if ultp is unavailable.
    """
    if not oi_snap:
        return None, None

    rows = oi_snap.get("rows", [])
    if not rows:
        return None, None

    strikes = sorted(r["strike"] for r in rows)

    # live_ultp comes from put-call parity in oi_tracker._compute_kpis()
    # and updates on every tick — far more current than the stored atm_strike.
    ultp = float(oi_snap.get("ultp") or 0)

    if ultp:
        dyn_atm = min(strikes, key=lambda s: abs(s - ultp))
        atm_idx = strikes.index(dyn_atm)

        if direction == "CE":
            target_idx = max(0, atm_idx - 1)           # 1 strike ITM = below spot
        else:
            target_idx = min(len(strikes) - 1, atm_idx + 1)  # 1 strike ITM = above spot

        target_strike = strikes[target_idx]
    else:
        # No live spot — fall back to stored atm_strike
        target_strike = oi_snap.get("atm_strike")
        if not target_strike:
            return None, None

    key = "ce_ltp" if direction == "CE" else "pe_ltp"
    for r in rows:
        if r.get("strike") == target_strike:
            ltp = r.get(key, 0)
            if ltp and ltp > 0:
                return round(float(ltp), 2), int(target_strike)

    return None, int(target_strike) if target_strike else None


def _round_premium(v: float) -> float:
    """Round option premium to nearest 0.5 (typical option tick)."""
    return round(round(v * 2) / 2, 1)


def _wait_reason(regime: str, phase: str, lin_score: int, health_score: int, vel_type: str) -> str:
    if vel_type == "FLAT":
        return f"Velocity flat — waiting for momentum ({regime} / {phase})"
    if lin_score < _MIN_LINEAR_SCORE:
        return f"Linear score {lin_score}/100 — need ≥{_MIN_LINEAR_SCORE} to trigger"
    if health_score < _MIN_HEALTH_SCORE:
        return f"Health {health_score}/100 — need ≥{_MIN_HEALTH_SCORE} to trigger"
    if phase not in _BUY_PHASE_TRIGGERS:
        return f"Phase is {phase} — waiting for BREAKOUT or TREND_RIDE"
    return f"Waiting for setup — {regime} / {phase} / score {lin_score}/100"


def _make(
    action, direction, instrument, atm_strike, entry, target, sl,
    reason, counter_reasons, regime, phase, health_score, lin_score, now,
) -> dict:
    return {
        "action":          action,
        "direction":       direction,
        "instrument":      instrument,
        "atm_strike":      atm_strike,
        "entry":           entry,
        "target":          target,
        "sl":              sl,
        "reason":          reason,
        "counter_reasons": counter_reasons,
        "regime":          regime,
        "phase":           phase,
        "health_score":    health_score,
        "lin_score":       lin_score,
        "generated_at":    now.strftime("%H:%M:%S"),
        "is_new":          False,
    }


def _dedup(instrument: str, sig: dict) -> dict:
    """
    Deduplication + price locking.

    Rules:
      - is_new=True on action/direction state change, or BUY cooldown reset.
      - On a new BUY: lock entry/target/sl/atm_strike/generated_at into state.
      - On a continuing BUY (is_new=False): restore locked prices — live LTP
        must NOT drift the levels after the signal is already showing.
      - NO_TRADE / WAIT have no prices to lock; pass through as-is.
    """
    prev = _signal_state.get(instrument, {})
    now  = datetime.now()

    state_changed = (
        sig["action"]    != prev.get("action") or
        sig["direction"] != prev.get("direction")
    )

    buy_cooldown_reset = False
    if not state_changed and sig["action"] == "BUY":
        prev_time = prev.get("time")
        if prev_time:
            elapsed = (now - prev_time).total_seconds() / 60
            buy_cooldown_reset = elapsed >= _COOLDOWN_MINUTES

    if state_changed or buy_cooldown_reset:
        # New or re-issued signal — lock current prices into state
        sig["is_new"] = True
        _signal_state[instrument] = {
            "action":       sig["action"],
            "direction":    sig["direction"],
            "time":         now,
            "entry":        sig.get("entry"),
            "target":       sig.get("target"),
            "sl":           sig.get("sl"),
            "atm_strike":   sig.get("atm_strike"),
            "generated_at": sig.get("generated_at"),
        }
    elif sig["action"] == "BUY":
        # Continuing BUY — restore frozen levels, never let live LTP change them
        sig["entry"]        = prev.get("entry")
        sig["target"]       = prev.get("target")
        sig["sl"]           = prev.get("sl")
        sig["atm_strike"]   = prev.get("atm_strike")
        sig["generated_at"] = prev.get("generated_at")

    return sig
