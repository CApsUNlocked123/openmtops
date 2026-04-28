"""
POV Engine — Pattern OI Velocity signal computation.

Evaluates a short-squeeze pattern on closed 1-minute option candles.
Pure computation — no Flask, no DB, no threads.

Pattern (5 conditions):
  PRE: sum of positive OI changes across last 3 candles >= 50,000
       (confirms trapped shorts exist — skip signal check if false)
  C1:  volume > rolling_mean(last 5 candles volume) * 3.0
  C2:  abs(oi_change this candle) < 30,000  [7% of total OI for MIDCPNIFTY]
  C3:  (high - low) > prev_candle (high - low) * 2.0
  C4:  (min(open,close) - low) / (high - low) < 0.15
  C5:  close > open

Score 5/5 → STRONG | Score 4/5 → WATCH | Score < 4 → WAIT (no signal)

On signal:
  entry = candle close
  sl    = candle low
  risk  = entry - sl
  t1    = entry + risk * 1.5
  t2    = entry + risk * 3.0
  t3    = entry + risk * 5.0
"""

from __future__ import annotations
from datetime import datetime

# ── Constants ─────────────────────────────────────────────────────────────────
_COOLDOWN_MINUTES = 15
_PRE_OI_MIN       = 50_000   # sum of positive OI changes across last 3 candles
_OI_ABS_THRESHOLD = 30_000   # C2: max abs OI change (non-MIDCPNIFTY)
_OI_PCT_MIDCP     = 0.07     # C2: max OI change as % of total OI (MIDCPNIFTY)
_VOL_MULT         = 3.0      # C1: volume spike multiplier vs 5-candle mean
_RANGE_MULT       = 2.0      # C3: range expansion vs previous candle
_WICK_MAX         = 0.15     # C4: max lower wick as fraction of range

# ── Per-instrument deduplication state ────────────────────────────────────────
_state: dict[str, dict] = {}   # sid → {action, time, fired_at}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def evaluate(sid: str, candles: list[dict], is_midcp: bool = False) -> dict:
    """
    Evaluate the short-squeeze pattern against the most recent closed candle.

    candles : list of dicts [{open,high,low,close,volume,oi,oi_change,time}],
              oldest first.  oi_change must be pre-computed by the caller
              (routes/pov.py sets it as current_oi - previous_candle_oi).
    is_midcp: True when the instrument is MIDCPNIFTY (use % OI threshold for C2).

    Returns a result dict — always safe to call, never raises.
    """
    wait = _make("WAIT", 0, False, False, False, False, False,
                 None, None, None, None, None, sid,
                 candles[-1] if candles else {})

    if len(candles) < 6:
        return wait

    cur  = candles[-1]
    prev = candles[-2]

    # ── PRE condition: recent positive OI buildup ─────────────────────────────
    pos_oi_sum = sum(
        max(0, (c.get("oi_change") or 0))
        for c in candles[-3:]
    )
    if pos_oi_sum < _PRE_OI_MIN:
        return _dedup(sid, wait)

    # ── C1: volume spike (3× rolling mean of last 5 candles) ─────────────────
    last5_vols = [(c.get("volume") or 0) for c in candles[-6:-1]]
    avg_vol    = sum(last5_vols) / len(last5_vols) if last5_vols else 0
    c1 = (cur.get("volume") or 0) > avg_vol * _VOL_MULT

    # ── C2: OI absorption — minor change signals trapped shorts not covering ──
    oi_chg = abs(cur.get("oi_change") or 0)
    if is_midcp:
        total_oi  = max(cur.get("oi") or 1, 1)
        threshold = total_oi * _OI_PCT_MIDCP
    else:
        threshold = _OI_ABS_THRESHOLD
    c2 = oi_chg < threshold

    # ── C3: range expansion vs previous candle ────────────────────────────────
    cur_range  = (cur.get("high")  or 0) - (cur.get("low")  or 0)
    prev_range = (prev.get("high") or 0) - (prev.get("low") or 0)
    c3 = (cur_range > prev_range * _RANGE_MULT) if prev_range > 0 else False

    # ── C4: lower wick < 15% of range (bullish body dominates floor) ─────────
    lo      = cur.get("low",   0) or 0
    op      = cur.get("open",  0) or 0
    cl      = cur.get("close", 0) or 0
    body_lo = min(op, cl)
    c4 = ((body_lo - lo) / cur_range < _WICK_MAX) if cur_range > 0 else False

    # ── C5: green candle ──────────────────────────────────────────────────────
    c5 = cl > op

    score  = sum([c1, c2, c3, c4, c5])
    action = "STRONG" if score == 5 else ("WATCH" if score == 4 else "WAIT")

    if score >= 4:
        entry = round(cl, 2)
        sl    = round(lo, 2)
        risk  = max(entry - sl, 0.5)
        t1    = round(entry + risk * 1.5, 2)
        t2    = round(entry + risk * 3.0, 2)
        t3    = round(entry + risk * 5.0, 2)
    else:
        entry = sl = t1 = t2 = t3 = None

    result = _make(action, score, c1, c2, c3, c4, c5,
                   entry, sl, t1, t2, t3, sid, cur)
    return _dedup(sid, result)


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _make(action, score, c1, c2, c3, c4, c5,
          entry, sl, t1, t2, t3, sid, candle) -> dict:
    return {
        "action":   action,
        "score":    score,
        "c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5,
        "entry":    entry,
        "sl":       sl,
        "t1":       t1,
        "t2":       t2,
        "t3":       t3,
        "sid":      sid,
        "fired_at": None,
        "is_new":   False,
        "candle":   candle,
    }


def _dedup(sid: str, result: dict) -> dict:
    """
    Deduplication + 15-minute cooldown.
    Mirrors the _dedup / is_new pattern from signal_engine.py.
    """
    now  = datetime.now()
    prev = _state.get(sid, {})

    action_changed = result["action"] != prev.get("action")

    cooldown_reset = False
    if not action_changed and result["action"] in ("STRONG", "WATCH"):
        prev_time = prev.get("time")
        if prev_time:
            elapsed = (now - prev_time).total_seconds() / 60
            cooldown_reset = elapsed >= _COOLDOWN_MINUTES

    if action_changed or cooldown_reset:
        fired_at = now.strftime("%H:%M:%S")
        result["is_new"]   = True
        result["fired_at"] = fired_at
        _state[sid] = {
            "action":   result["action"],
            "time":     now,
            "fired_at": fired_at,
        }
    elif result["action"] in ("STRONG", "WATCH"):
        result["fired_at"] = prev.get("fired_at")

    return result
