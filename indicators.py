"""
OI analysis indicators for Indian index options.
Max pain, PCR, wall classification, signals.
"""

import numpy as np
import pandas as pd


def build_oi_df(chain: dict) -> pd.DataFrame:
    def _oi(s):   return int(s.get("oi") or 0)
    def _ltp(s):  return float(s.get("last_price") or 0)
    def _iv(s):   return float(s.get("implied_volatility") or 0)
    def _g(s, k): return float((s.get("greeks") or {}).get(k) or 0)
    def _sid(s):  return str(int(s["security_id"])) if s.get("security_id") else ""

    rows = []
    for strike, data in chain.items():
        ce = data.get("ce") or {}
        pe = data.get("pe") or {}
        rows.append({
            "strike":   int(strike),
            "ce_oi":    _oi(ce),   "pe_oi":    _oi(pe),
            "ce_ltp":   _ltp(ce),  "pe_ltp":   _ltp(pe),
            "ce_iv":    _iv(ce),   "pe_iv":    _iv(pe),
            "ce_delta": _g(ce, "delta"),
            "pe_delta": _g(pe, "delta"),
            "ce_sid":   _sid(ce),  "pe_sid":   _sid(pe),
        })

    df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    df["total_oi"] = df["ce_oi"] + df["pe_oi"]
    return df


def calculate_max_pain(df: pd.DataFrame) -> int:
    strikes = df["strike"].values
    ce_oi   = df["ce_oi"].values.astype(float)
    pe_oi   = df["pe_oi"].values.astype(float)
    pain = np.array([
        np.sum(np.maximum(0, s - strikes) * ce_oi) +
        np.sum(np.maximum(0, strikes - s) * pe_oi)
        for s in strikes
    ])
    return int(strikes[np.argmin(pain)])


def calculate_pcr(df: pd.DataFrame) -> float:
    total_ce = df["ce_oi"].sum()
    return round(df["pe_oi"].sum() / total_ce, 3) if total_ce > 0 else 0.0


def classify_pcr(pcr: float) -> str:
    if pcr >= 1.3:  return "BULLISH"
    if pcr >= 1.0:  return "MILDLY_BULLISH"
    if pcr >= 0.7:  return "NEUTRAL"
    if pcr >= 0.5:  return "MILDLY_BEARISH"
    return "BEARISH"


def classify_oi_levels(df, put_wall_ratio=2.0, call_wall_ratio=2.0, tier1_count=3):
    active = df[df["total_oi"] > 0].sort_values("total_oi", ascending=False)
    if active.empty:
        return []
    tier1_cutoff = active["total_oi"].iloc[min(tier1_count - 1, len(active) - 1)]
    tier2_cutoff = active.head(tier1_count)["total_oi"].mean() * 1.5
    levels = []
    for _, row in active.iterrows():
        strike = int(row["strike"])
        ce = float(row["ce_oi"])
        pe = float(row["pe_oi"])
        tot = float(row["total_oi"])
        tier = 1 if tot >= tier1_cutoff else (2 if tot >= tier2_cutoff else 3)
        r_ce = (ce / pe) if pe > 0 else float("inf")
        r_pe = (pe / ce) if ce > 0 else float("inf")
        if r_ce >= call_wall_ratio and r_pe >= put_wall_ratio:
            cls = "FORTRESS"
        elif r_ce >= call_wall_ratio:
            cls = "CALL_WALL"
        elif r_pe >= put_wall_ratio:
            cls = "PUT_WALL"
        elif ce >= pe:
            cls = "RESISTANCE"
        else:
            cls = "SUPPORT"
        levels.append({
            "strike": strike, "ce_oi": ce, "pe_oi": pe,
            "total_oi": tot, "classification": cls, "tier": tier,
            "ce_pe_ratio": round(r_ce, 2) if r_ce != float("inf") else 999,
            "pe_ce_ratio": round(r_pe, 2) if r_pe != float("inf") else 999,
        })
    return levels


def assess_oi_clarity(levels):
    t1 = [l for l in levels if l["tier"] == 1]
    if not t1:        return "NO_MAP"
    if len(t1) == 1:  return "CLEAR"
    ratio = t1[0]["total_oi"] / t1[1]["total_oi"] if t1[1]["total_oi"] > 0 else 999
    return "CLEAR" if ratio >= 2.0 else "MIXED"


def generate_signals(df, spot, max_pain, pcr, pcr_bias, levels, clarity):
    if df.empty or spot <= 0 or not levels:
        return []
    tier1  = [l for l in levels if l["tier"] == 1]
    ranked = sorted(levels, key=lambda l: l["total_oi"], reverse=True)
    now    = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    signals = []

    for level in ranked[:8]:
        strike = level["strike"]
        dist   = (strike - spot) / spot
        adist  = abs(dist)
        if adist < 0.005 or adist > 0.05:
            continue
        direction = "LONG" if dist > 0 else "SHORT"
        cls = level["classification"]
        valid = (
            (direction == "LONG"  and cls in ("PUT_WALL", "FORTRESS")) or
            (direction == "SHORT" and cls in ("CALL_WALL", "FORTRESS")) or
            abs(strike - max_pain) / max(spot, 1) <= 0.005
        )
        if not valid: continue
        if direction == "LONG"  and pcr < 0.6: continue
        if direction == "SHORT" and pcr > 1.4: continue
        blocking = any(
            (direction == "LONG"  and spot < o["strike"] < strike and o["classification"] == "CALL_WALL") or
            (direction == "SHORT" and strike < o["strike"] < spot  and o["classification"] == "PUT_WALL")
            for o in tier1 if o["strike"] != strike
        )
        if blocking: continue

        met, failed, conf = [], [], 0
        met.append(f"Target {cls} at {strike:,} ({dist:+.2%})")
        if len(ranked) >= 2 and level["total_oi"] > 1.8 * ranked[1]["total_oi"]:
            conf += 1; met.append("OI dominant")
        else:
            failed.append("OI not dominant")
        if (direction == "LONG" and pcr >= 1.3) or (direction == "SHORT" and pcr <= 0.7):
            conf += 1; met.append(f"PCR aligned: {pcr:.2f}")
        else:
            failed.append(f"PCR neutral: {pcr:.2f}")
        if (direction == "LONG" and max_pain > spot) or (direction == "SHORT" and max_pain < spot):
            conf += 1; met.append(f"Max pain aligned: {max_pain:,}")
        else:
            failed.append(f"Max pain misaligned: {max_pain:,}")
        if clarity == "CLEAR":
            conf += 1; met.append("Clarity: CLEAR")
        else:
            failed.append(f"Clarity: {clarity}")
        if adist <= 0.02:
            conf += 1; met.append(f"Close target ({adist:.2%})")
        else:
            failed.append(f"Far target ({adist:.2%})")

        stop_pct = max(0.004, min(0.015, adist * 0.5))
        if direction == "LONG":
            stop_loss = round(spot * (1 - stop_pct), 2); target = round(strike * 0.997, 2)
        else:
            stop_loss = round(spot * (1 + stop_pct), 2); target = round(strike * 1.003, 2)

        signals.append({
            "signal_type": direction, "confidence": int(conf / 5 * 100),
            "entry_price": round(spot, 2), "target_price": target, "stop_loss": stop_loss,
            "setup": "A: OI Magnet Pull", "met_conditions": met, "failed_conditions": failed,
            "key_metrics": {"pcr": pcr, "pcr_bias": pcr_bias, "max_pain": max_pain,
                            "clarity": clarity, "target_oi": level["total_oi"],
                            "classification": cls, "distance_pct": round(dist * 100, 2)},
            "timestamp": now,
        })

    mp_dist = (max_pain - spot) / spot
    if 0.003 < abs(mp_dist) < 0.06:
        direction = "LONG" if mp_dist > 0 else "SHORT"
        if not (direction == "LONG" and pcr < 0.6) and not (direction == "SHORT" and pcr > 1.4):
            met, failed, conf = [], [], 2
            met.append(f"Max pain {max_pain:,} is {abs(mp_dist):.2%} away")
            met.append(f"PCR: {pcr:.2f}")
            if clarity == "CLEAR":
                conf += 1; met.append("Clarity: CLEAR")
            else:
                failed.append(f"Clarity: {clarity}")
            blocking = any(
                (direction == "LONG"  and spot < l["strike"] < max_pain and l["classification"] == "CALL_WALL") or
                (direction == "SHORT" and max_pain < l["strike"] < spot  and l["classification"] == "PUT_WALL")
                for l in tier1
            )
            if not blocking:
                conf += 1; met.append("No blocking wall")
            else:
                failed.append("Blocking wall in path")
            stop_pct  = max(0.005, abs(mp_dist) * 0.6)
            stop_loss = round(spot * (1 - stop_pct if direction == "LONG" else 1 + stop_pct), 2)
            signals.append({
                "signal_type": direction, "confidence": int(conf / 5 * 100),
                "entry_price": round(spot, 2), "target_price": max_pain, "stop_loss": stop_loss,
                "setup": "B: Max Pain Gravity", "met_conditions": met, "failed_conditions": failed,
                "key_metrics": {"pcr": pcr, "pcr_bias": pcr_bias, "max_pain": max_pain,
                                "clarity": clarity, "distance_pct": round(mp_dist * 100, 2),
                                "classification": "MAX_PAIN"},
                "timestamp": now,
            })

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    return signals
