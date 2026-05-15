"""
Narrative generator for option-chain tape reading.

Consumes the structured payload produced by `routes.analyzer.load_series()` and
produces a list of bullets that each describe ONE observable event in plain
English. Every bullet is:

  - independent (one template, one fact)
  - reproducible (the supporting numbers are printed in the text)
  - inspectable (carries strike + time range + metric list so the UI can
    highlight the exact minutes on the exact chart)

Design rules:
  - Every threshold is a named constant at the top with a comment.
  - Templates print their evidence inline — never trust a sentence without
    numbers to back it up.
  - Templates skip the first WARMUP_MIN minutes (low OI base inflates % deltas).
  - Templates skip values where the underlying data is missing.
  - Adjacent strikes with the same action are clustered into one sentence.
"""

from __future__ import annotations
from typing import Optional


# ── Tunable thresholds ───────────────────────────────────────────────────────
# These are intentionally conservative. Each bullet prints its actual numbers
# so a user can spot a too-loose threshold immediately.

WARMUP_MIN          = 15      # skip first N minutes (OI base is too small)
OI_MIN_ABSOLUTE     = 50_000  # ignore strikes with OI below this (low signal)
WINDOW_MIN          = 15      # look back this many minutes for "fresh" actions

OI_STRONG_PCT       = 5.0     # OI Δ ≥ this over the window → "strong"
OI_MODERATE_PCT     = 2.5     # OI Δ ≥ this over the window → "moderate"
OI_MILD_PCT         = 1.0     # OI Δ ≥ this over the window → "mild"

PRICE_SIG_PCT       = 3.0     # min option price Δ to call a direction "real"

IV_STABLE_STDEV     = 1.0     # IV stdev within the window ≤ this → stable
IV_RISE_PCT         = 8.0     # IV pct rise over window ≥ this → "rising"
IV_VALID_LO         = 5.0     # ignore IV values outside [LO, HI] (solver junk)
IV_VALID_HI         = 200.0

VOL_SPIKE_RATIO     = 1.5     # bar volume ≥ this × rolling median → "spike"
VOL_DEAD_RATIO      = 0.4     # bar volume ≤ this × rolling median → "dead"

DIVERG_SPOT_MIN_PCT = 0.025   # min |Δspot%| to consider the move "real" (~6pt Nifty)
DIVERG_OPT_MIN_PCT  = 1.5     # min |Δoption%| to consider the divergence "real"
DIVERG_IV_RISE_MIN  = 2.0     # IV must rise by ≥ this much to confirm buying

REJECT_PROXIMITY_PCT = 0.10   # spot within X% of a wall and reverses → rejection

REGIME_IV_PCT       = 5.0     # avg IV chain expanded/compressed by ≥ this


# ── Severity tags + emoji-free badge colors (the JS layer picks the color) ──

SEVERITY_STRONG   = "strong"
SEVERITY_MODERATE = "moderate"
SEVERITY_MILD     = "mild"

# Category tags drive the colored bracket in the UI: [WALL], [DIVERG], etc.
CAT_WALL    = "WALL"
CAT_DIVERG  = "DIVERG"
CAT_VOLUME  = "VOLUME"
CAT_SPOT    = "SPOT"
CAT_CLUSTER = "CLUSTER"
CAT_REGIME  = "REGIME"


# ── Numeric helpers ─────────────────────────────────────────────────────────

def _valid_iv(v: Optional[float]) -> bool:
    return v is not None and IV_VALID_LO <= v <= IV_VALID_HI


def _pct(numer: float, denom: float) -> Optional[float]:
    if denom == 0 or denom is None:
        return None
    return (numer / denom) * 100.0


def _median(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def _last_non_none(xs: list, default=None):
    for x in reversed(xs):
        if x is not None:
            return x
    return default


def _stdev(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    return (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5


def _fmt_inr(v: float) -> str:
    """Format a price as INR string."""
    return f"₹{v:,.2f}" if v >= 1 else f"₹{v:.2f}"


def _fmt_oi(v: int) -> str:
    if v >= 1e7:
        return f"{v/1e7:.2f}Cr"
    if v >= 1e5:
        return f"{v/1e5:.2f}L"
    return f"{v:,}"


def _fmt_hhmm(epoch: int) -> str:
    """Epoch seconds → HH:MM IST."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.fromtimestamp(epoch, tz=ist).strftime("%H:%M")


def _bullet(text: str, *, category: str, severity: str,
            strikes: list[int], sides: list[str],
            time_from: Optional[int] = None, time_to: Optional[int] = None,
            metrics: Optional[list[str]] = None) -> dict:
    """Standard bullet shape — the UI's contract."""
    return {
        "text":      text,
        "category":  category,
        "severity":  severity,
        "strikes":   strikes,
        "sides":     sides,
        "time_from": time_from,
        "time_to":   time_to,
        "metrics":   metrics or [],
    }


# ── Window helpers ──────────────────────────────────────────────────────────

def _window_slice(leg: dict, n: int = WINDOW_MIN) -> tuple[int, int]:
    """Indices [start, end) covering the last `n` bars after the warmup."""
    total = len(leg.get("timestamps", []))
    if total < WARMUP_MIN + 3:
        return (0, 0)
    end   = total
    start = max(WARMUP_MIN, total - n)
    return (start, end)


def _has_oi_signal(leg: dict) -> bool:
    """Skip strikes with no meaningful OI base (avoids noise from illiquid strikes)."""
    latest_oi = _last_non_none([x for x in leg.get("oi", []) if x > 0])
    return latest_oi is not None and latest_oi >= OI_MIN_ABSOLUTE


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 1: Per-strike OI action over the last WINDOW_MIN minutes.
# Detects fresh writing / buying / unwinding by comparing windowed Δprice
# vs ΔOI and labeling with severity by the magnitude of the OI change.
# ────────────────────────────────────────────────────────────────────────────
def _action_label(d_price_pct: float, d_oi_pct: float) -> Optional[str]:
    if abs(d_oi_pct) < OI_MILD_PCT or abs(d_price_pct) < PRICE_SIG_PCT / 3:
        return None
    if d_oi_pct > 0 and d_price_pct > 0:  return "long buildup"
    if d_oi_pct > 0 and d_price_pct < 0:  return "fresh writing"
    if d_oi_pct < 0 and d_price_pct > 0:  return "short covering"
    if d_oi_pct < 0 and d_price_pct < 0:  return "long unwinding"
    return None


def _severity_for_oi(d_oi_pct: float) -> str:
    a = abs(d_oi_pct)
    if a >= OI_STRONG_PCT:   return SEVERITY_STRONG
    if a >= OI_MODERATE_PCT: return SEVERITY_MODERATE
    return SEVERITY_MILD


def template_strike_actions(strikes: list[dict]) -> list[dict]:
    out = []
    for strike_rec in strikes:
        strike = strike_rec["strike"]
        for side in ("ce", "pe"):
            leg = strike_rec[side]
            if not _has_oi_signal(leg):
                continue
            s, e = _window_slice(leg)
            if e - s < 5:
                continue
            prices = leg["price"][s:e]
            ois    = leg["oi"][s:e]
            ivs    = leg["iv"][s:e]
            ts     = leg["timestamps"][s:e]
            # Use first/last non-zero values in window
            p0 = next((p for p in prices if p > 0), None)
            p1 = next((p for p in reversed(prices) if p > 0), None)
            o0 = next((o for o in ois    if o > 0), None)
            o1 = next((o for o in reversed(ois)    if o > 0), None)
            if not all([p0, p1, o0, o1]):
                continue
            dp_pct = _pct(p1 - p0, p0) or 0.0
            do_pct = _pct(o1 - o0, o0) or 0.0
            label  = _action_label(dp_pct, do_pct)
            if not label:
                continue
            # IV note (optional context)
            iv_valid = [v for v in ivs if _valid_iv(v)]
            iv_note  = ""
            if iv_valid:
                iv_sd = _stdev(iv_valid) or 0
                iv_mean = sum(iv_valid) / len(iv_valid)
                if iv_sd <= IV_STABLE_STDEV:
                    iv_note = f", IV stable at {iv_mean:.1f}%"
                elif iv_valid[-1] - iv_valid[0] >= IV_RISE_PCT:
                    iv_note = f", IV rising {iv_valid[0]:.1f}% → {iv_valid[-1]:.1f}%"
                elif iv_valid[0] - iv_valid[-1] >= IV_RISE_PCT:
                    iv_note = f", IV falling {iv_valid[0]:.1f}% → {iv_valid[-1]:.1f}%"

            text = (
                f"{strike:,} {side.upper()}: {label} "
                f"(OI {_fmt_oi(o0)} → {_fmt_oi(o1)}, {do_pct:+.1f}%; "
                f"price {_fmt_inr(p0)} → {_fmt_inr(p1)}, {dp_pct:+.1f}%{iv_note}) "
                f"between {_fmt_hhmm(ts[0])} and {_fmt_hhmm(ts[-1])}"
            )
            out.append(_bullet(
                text,
                category=CAT_WALL,
                severity=_severity_for_oi(do_pct),
                strikes=[strike],
                sides=[side],
                time_from=ts[0], time_to=ts[-1],
                metrics=["oi", "price", "iv"],
            ))
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 2: Cluster adjacent strikes with the SAME action into one sentence.
# Reduces noise when 3 nearby strikes all show "mild writing" — one bullet
# instead of three. Runs as a post-pass on template_strike_actions output.
# ────────────────────────────────────────────────────────────────────────────
def cluster_adjacent_actions(action_bullets: list[dict]) -> list[dict]:
    """Collapse multiple bullets that say the same thing across adjacent strikes.

    Two cases:
      - 2+ mild bullets with same action → single CLUSTER bullet ("thin side")
      - 3+ bullets (any severity) with same action AND adjacent strikes →
        single CLUSTER bullet ("widespread pattern")
    Otherwise leave bullets alone.
    """
    if not action_bullets:
        return []
    grouped: dict[tuple, list[dict]] = {}
    for b in action_bullets:
        try:
            after_colon = b["text"].split(":", 1)[1]
            action = after_colon.split("(")[0].strip().split(",")[0].strip()
        except IndexError:
            action = "unknown"
        key = (b["sides"][0], action)
        grouped.setdefault(key, []).append(b)

    out: list[dict] = []
    for (side, action), bs in grouped.items():
        if len(bs) <= 1:
            out.extend(bs)
            continue
        bs_sorted = sorted(bs, key=lambda x: x["strikes"][0])
        all_mild  = all(b["severity"] == SEVERITY_MILD for b in bs_sorted)

        if all_mild and len(bs_sorted) >= 2:
            strikes = [b["strikes"][0] for b in bs_sorted]
            t_from = min((b["time_from"] for b in bs_sorted if b["time_from"]), default=None)
            t_to   = max((b["time_to"]   for b in bs_sorted if b["time_to"]),   default=None)
            text = (
                f"On {side.upper()} strikes {', '.join(f'{s:,}' for s in strikes)}, "
                f"only mild {action} observed — this side of the chain is thin."
            )
            out.append(_bullet(
                text, category=CAT_CLUSTER, severity=SEVERITY_MILD,
                strikes=strikes, sides=[side],
                time_from=t_from, time_to=t_to, metrics=["oi", "price"],
            ))
        elif len(bs_sorted) >= 3:
            # Widespread same-direction action — emit one cluster bullet that
            # *replaces* the individual ones (otherwise the user is reading 3+
            # rows that say the same thing).
            strikes = [b["strikes"][0] for b in bs_sorted]
            t_from = min((b["time_from"] for b in bs_sorted if b["time_from"]), default=None)
            t_to   = max((b["time_to"]   for b in bs_sorted if b["time_to"]),   default=None)
            text = (
                f"Widespread {action} on {side.upper()} side across strikes "
                f"{', '.join(f'{s:,}' for s in strikes)} — coordinated positioning."
            )
            out.append(_bullet(
                text, category=CAT_CLUSTER, severity=SEVERITY_STRONG,
                strikes=strikes, sides=[side],
                time_from=t_from, time_to=t_to, metrics=["oi", "price"],
            ))
        else:
            out.extend(bs_sorted)
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 3: IV regime per strike — explicit interpretation.
# Different from the in-line IV note above: this one stands on its own as a
# "writer confidence" or "demand-driven" signal.
# ────────────────────────────────────────────────────────────────────────────
def template_iv_regime(strikes: list[dict]) -> list[dict]:
    out = []
    for strike_rec in strikes:
        strike = strike_rec["strike"]
        for side in ("ce", "pe"):
            leg = strike_rec[side]
            ivs = leg.get("iv", [])
            ts  = leg.get("timestamps", [])
            if len(ivs) < WARMUP_MIN + 5:
                continue
            # Skip strikes with negligible OI — IV "stability" there is just
            # noise from an inactive instrument.
            if not _has_oi_signal(leg):
                continue
            # Also require some price activity in the window — a flat-line price
            # means the BS solver returned the same IV repeatedly and "stable"
            # tells us nothing about writer confidence.
            window_prices = [p for p in leg["price"][-WINDOW_MIN:] if p > 0]
            if len(window_prices) >= 2:
                pmin, pmax = min(window_prices), max(window_prices)
                if pmin > 0 and (pmax - pmin) / pmin * 100 < 1.0:
                    continue   # price barely moved → IV stability is meaningless

            tail = [(t, v) for t, v in zip(ts[-WINDOW_MIN:], ivs[-WINDOW_MIN:])
                    if _valid_iv(v)]
            if len(tail) < 5:
                continue
            values = [v for _, v in tail]
            sd     = _stdev(values) or 0
            mean   = sum(values) / len(values)
            v0, v1 = values[0], values[-1]
            t0, t1 = tail[0][0], tail[-1][0]

            if sd <= IV_STABLE_STDEV:
                interp = "writers are confident — level holding"
                cat_sev = SEVERITY_MODERATE
                text = (f"{strike:,} {side.upper()} IV is stable at "
                        f"{mean:.1f}% ± {sd:.2f} — {interp}.")
            elif v1 - v0 >= IV_RISE_PCT:
                interp = "demand outpacing supply — directional pressure"
                cat_sev = SEVERITY_STRONG
                text = (f"{strike:,} {side.upper()} IV rising sharply "
                        f"{v0:.1f}% → {v1:.1f}% (+{v1-v0:.1f}pts) — {interp}.")
            elif v0 - v1 >= IV_RISE_PCT:
                interp = "premium decay / writer dominance"
                cat_sev = SEVERITY_MODERATE
                text = (f"{strike:,} {side.upper()} IV falling "
                        f"{v0:.1f}% → {v1:.1f}% ({v1-v0:+.1f}pts) — {interp}.")
            else:
                continue  # not a notable regime

            out.append(_bullet(
                text,
                category=CAT_REGIME,
                severity=cat_sev,
                strikes=[strike],
                sides=[side],
                time_from=t0, time_to=t1,
                metrics=["iv"],
            ))
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 4: Volume confirmation — strike with the largest recent volume
# spike relative to its own rolling median. One bullet per spike.
# ────────────────────────────────────────────────────────────────────────────
def template_volume_spikes(strikes: list[dict]) -> list[dict]:
    out = []
    for strike_rec in strikes:
        strike = strike_rec["strike"]
        for side in ("ce", "pe"):
            leg = strike_rec[side]
            vols = leg.get("volume", [])
            ts   = leg.get("timestamps", [])
            if len(vols) < WARMUP_MIN + WINDOW_MIN:
                continue
            # Compare last bar to rolling median over previous WINDOW_MIN bars
            recent_window = vols[-WINDOW_MIN-1:-1]
            last_bar      = vols[-1]
            med = _median(recent_window)
            if not med or med <= 0 or last_bar <= 0:
                continue
            ratio = last_bar / med
            if ratio >= VOL_SPIKE_RATIO:
                text = (f"{strike:,} {side.upper()} volume at last bar "
                        f"({_fmt_hhmm(ts[-1])}) is {ratio:.1f}× its 15-min median "
                        f"({last_bar:,} vs median {med:,.0f}) — institutional participation.")
                out.append(_bullet(
                    text,
                    category=CAT_VOLUME,
                    severity=SEVERITY_STRONG if ratio >= 2.5 else SEVERITY_MODERATE,
                    strikes=[strike],
                    sides=[side],
                    time_from=ts[-WINDOW_MIN], time_to=ts[-1],
                    metrics=["volume"],
                ))
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 5: Wall identification — strongest CE wall above spot, strongest
# PE floor below spot. Uses the latest OI snapshot.
# ────────────────────────────────────────────────────────────────────────────
def template_walls(strikes: list[dict], spot: float) -> list[dict]:
    if not spot:
        return []
    # Latest OI per (strike, side)
    snapshot = []
    for sr in strikes:
        k = sr["strike"]
        ce_oi = _last_non_none([o for o in sr["ce"].get("oi", []) if o > 0]) or 0
        pe_oi = _last_non_none([o for o in sr["pe"].get("oi", []) if o > 0]) or 0
        snapshot.append({"strike": k, "ce_oi": ce_oi, "pe_oi": pe_oi})

    out = []
    # CE wall = highest CE OI strictly above spot
    ces = [s for s in snapshot if s["strike"] > spot and s["ce_oi"] > 0]
    if ces:
        ce_wall = max(ces, key=lambda s: s["ce_oi"])
        text = (f"CE wall (resistance) sits at {ce_wall['strike']:,} "
                f"with {_fmt_oi(ce_wall['ce_oi'])} OI — "
                f"spot is {ce_wall['strike'] - spot:.0f} pts below.")
        out.append(_bullet(
            text, category=CAT_WALL, severity=SEVERITY_STRONG,
            strikes=[ce_wall["strike"]], sides=["ce"],
            metrics=["oi"],
        ))
    # PE floor = highest PE OI strictly below spot
    pes = [s for s in snapshot if s["strike"] < spot and s["pe_oi"] > 0]
    if pes:
        pe_floor = max(pes, key=lambda s: s["pe_oi"])
        text = (f"PE floor (support) sits at {pe_floor['strike']:,} "
                f"with {_fmt_oi(pe_floor['pe_oi'])} OI — "
                f"spot is {spot - pe_floor['strike']:.0f} pts above.")
        out.append(_bullet(
            text, category=CAT_WALL, severity=SEVERITY_STRONG,
            strikes=[pe_floor["strike"]], sides=["pe"],
            metrics=["oi"],
        ))
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 6: Spot-vs-wall rejection events. Scan the spot series for minutes
# where spot got within REJECT_PROXIMITY_PCT of a wall and then reversed.
# ────────────────────────────────────────────────────────────────────────────
def template_spot_rejections(strikes: list[dict], spot_series: dict, spot: float) -> list[dict]:
    times = spot_series.get("timestamps", [])
    closes = spot_series.get("close", [])
    if len(times) < 5 or not spot:
        return []

    # Identify CE wall + PE floor from latest OI
    snapshot = []
    for sr in strikes:
        ce_oi = _last_non_none([o for o in sr["ce"].get("oi", []) if o > 0]) or 0
        pe_oi = _last_non_none([o for o in sr["pe"].get("oi", []) if o > 0]) or 0
        snapshot.append({"strike": sr["strike"], "ce_oi": ce_oi, "pe_oi": pe_oi})

    ces = [s for s in snapshot if s["strike"] > spot and s["ce_oi"] > 0]
    pes = [s for s in snapshot if s["strike"] < spot and s["pe_oi"] > 0]
    ce_wall  = max(ces, key=lambda s: s["ce_oi"])["strike"] if ces else None
    pe_floor = max(pes, key=lambda s: s["pe_oi"])["strike"] if pes else None

    out = []
    proximity_pts = spot * REJECT_PROXIMITY_PCT / 100.0   # ~24pts on Nifty

    def scan(wall: int, side: str, label: str):
        if not wall:
            return
        # find local extrema within proximity that then reversed by 0.15%
        best = None
        for i in range(2, len(closes) - 2):
            if side == "ce":
                if closes[i] >= wall - proximity_pts and closes[i] > max(closes[i+1], closes[i+2]):
                    revert_pct = (closes[i] - max(closes[i+1], closes[i+2])) / closes[i] * 100
                    if revert_pct >= 0.15 and (not best or closes[i] > best["high"]):
                        best = {"i": i, "high": closes[i], "rev_low": min(closes[i+1:i+5])}
            else:
                if closes[i] <= wall + proximity_pts and closes[i] < min(closes[i+1], closes[i+2]):
                    revert_pct = (min(closes[i+1], closes[i+2]) - closes[i]) / closes[i] * 100
                    if revert_pct >= 0.15 and (not best or closes[i] < best.get("low", float("inf"))):
                        best = {"i": i, "low": closes[i], "rev_high": max(closes[i+1:i+5])}
        if not best:
            return
        i = best["i"]
        if side == "ce":
            text = (f"Spot rejected at {best['high']:.0f} ({_fmt_hhmm(times[i])}) — "
                    f"{wall - best['high']:.0f} pts off the {wall:,} CE wall, "
                    f"then fell to {best['rev_low']:.0f}.")
        else:
            text = (f"Spot bounced at {best['low']:.0f} ({_fmt_hhmm(times[i])}) — "
                    f"{best['low'] - wall:.0f} pts off the {wall:,} PE floor, "
                    f"then rose to {best['rev_high']:.0f}.")
        out.append(_bullet(
            text, category=CAT_SPOT, severity=SEVERITY_MODERATE,
            strikes=[wall], sides=[side],
            time_from=times[max(0, i-2)], time_to=times[min(len(times)-1, i+4)],
            metrics=["price"],
        ))

    scan(ce_wall, "ce", "CE")
    scan(pe_floor, "pe", "PE")
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 7: Divergent option buying — the explicit user ask.
# Spot moves one way, option moves the "wrong" way, IV confirms it's demand.
# ────────────────────────────────────────────────────────────────────────────
def template_divergences(strikes: list[dict], spot_series: dict) -> list[dict]:
    spot_ts    = spot_series.get("timestamps", [])
    spot_close = spot_series.get("close", [])
    if len(spot_ts) < 3:
        return []
    spot_map = dict(zip(spot_ts, spot_close))

    out = []
    for sr in strikes:
        strike = sr["strike"]
        for side in ("ce", "pe"):
            leg = sr[side]
            ts     = leg.get("timestamps", [])
            prices = leg.get("price", [])
            ivs    = leg.get("iv", [])
            if len(ts) < WARMUP_MIN + 2:
                continue
            best_event = None
            for i in range(WARMUP_MIN, len(ts) - 1):
                t_now, t_prev = ts[i], ts[i-1]
                s_now  = spot_map.get(t_now)
                s_prev = spot_map.get(t_prev)
                p_now, p_prev = prices[i], prices[i-1]
                if not (s_now and s_prev and p_now and p_prev):
                    continue
                if p_prev == 0 or s_prev == 0:
                    continue
                d_spot_pct = (s_now - s_prev) / s_prev * 100.0
                d_opt_pct  = (p_now - p_prev) / p_prev * 100.0
                if abs(d_spot_pct) < DIVERG_SPOT_MIN_PCT:
                    continue
                if abs(d_opt_pct) < DIVERG_OPT_MIN_PCT:
                    continue
                # Expected sign: CE moves with spot, PE moves against
                expected_with_spot = (side == "ce")
                actual_with_spot   = (d_opt_pct > 0) == (d_spot_pct > 0)
                if actual_with_spot == expected_with_spot:
                    continue   # not divergent
                # IV must confirm (rising at least DIVERG_IV_RISE_MIN points)
                iv_now  = ivs[i]   if i   < len(ivs) else None
                iv_prev = ivs[i-1] if i-1 < len(ivs) else None
                if not (_valid_iv(iv_now) and _valid_iv(iv_prev)):
                    continue
                if iv_now - iv_prev < DIVERG_IV_RISE_MIN:
                    continue
                magnitude = abs(d_opt_pct)
                if not best_event or magnitude > best_event["mag"]:
                    best_event = {
                        "i": i, "mag": magnitude,
                        "s_now": s_now, "s_prev": s_prev,
                        "p_now": p_now, "p_prev": p_prev,
                        "iv_now": iv_now, "iv_prev": iv_prev,
                        "d_spot_pct": d_spot_pct, "d_opt_pct": d_opt_pct,
                        "t": t_now,
                    }
            if not best_event:
                continue
            e = best_event
            spot_dir = "rose" if e["d_spot_pct"] > 0 else "fell"
            opt_dir  = "rose" if e["d_opt_pct"]  > 0 else "fell"
            text = (
                f"At {_fmt_hhmm(e['t'])}, spot {spot_dir} from {e['s_prev']:.0f} "
                f"to {e['s_now']:.0f} ({e['d_spot_pct']:+.2f}%) but {strike:,} {side.upper()} "
                f"{opt_dir} {_fmt_inr(e['p_prev'])} → {_fmt_inr(e['p_now'])} "
                f"({e['d_opt_pct']:+.1f}%), with IV jumping "
                f"{e['iv_prev']:.1f}% → {e['iv_now']:.1f}% — "
                f"aggressive {side.upper()} buying despite unfavorable spot move."
            )
            out.append(_bullet(
                text, category=CAT_DIVERG, severity=SEVERITY_STRONG,
                strikes=[strike], sides=[side],
                time_from=ts[max(0, e["i"]-1)], time_to=e["t"],
                metrics=["price", "iv"],
            ))
    return out


# ────────────────────────────────────────────────────────────────────────────
# TEMPLATE 8: IV-chain regime — average IV across all CE strikes (or all PE)
# expanded or compressed since session open. Captures "premiums are getting
# expensive / cheap" as a single sentence.
# ────────────────────────────────────────────────────────────────────────────
def template_chain_regime(strikes: list[dict]) -> list[dict]:
    def avg_iv(side: str, idx: int) -> Optional[float]:
        vals = []
        for sr in strikes:
            ivs = sr[side].get("iv", [])
            if idx < len(ivs) and _valid_iv(ivs[idx]):
                vals.append(ivs[idx])
        return sum(vals) / len(vals) if vals else None

    out = []
    if not strikes:
        return out
    sample_len = len(strikes[0]["ce"].get("iv", []))
    if sample_len < WARMUP_MIN + 5:
        return out

    for side in ("ce", "pe"):
        iv_open = avg_iv(side, WARMUP_MIN)
        iv_now  = avg_iv(side, sample_len - 1)
        if iv_open is None or iv_now is None:
            continue
        d_pct = (iv_now - iv_open) / iv_open * 100.0 if iv_open > 0 else 0
        if abs(d_pct) < REGIME_IV_PCT:
            continue
        verb     = "expanded" if d_pct > 0 else "compressed"
        interp   = ("premiums getting expensive — straddle/range environment"
                    if d_pct > 0 else "premiums cheapening — directional move likely")
        text = (f"Average {side.upper()} IV across the chain has {verb} "
                f"{iv_open:.1f}% → {iv_now:.1f}% ({d_pct:+.1f}%) since session open — {interp}.")
        out.append(_bullet(
            text, category=CAT_REGIME,
            severity=SEVERITY_MODERATE if abs(d_pct) < 10 else SEVERITY_STRONG,
            strikes=[sr["strike"] for sr in strikes],
            sides=[side],
            metrics=["iv"],
        ))
    return out


def consolidate_iv_regime(iv_bullets: list[dict]) -> list[dict]:
    """If most strikes on a side say "IV stable," collapse them into one bullet
    so the user isn't reading 14 near-identical sentences."""
    if not iv_bullets:
        return []
    by_side_state: dict[tuple, list[dict]] = {}
    other: list[dict] = []
    for b in iv_bullets:
        side = b["sides"][0] if b.get("sides") else None
        if "stable" in b["text"] and side:
            by_side_state.setdefault((side, "stable"), []).append(b)
        else:
            other.append(b)

    consolidated: list[dict] = []
    for (side, _state), bs in by_side_state.items():
        if len(bs) >= 3:
            strikes = sorted(b["strikes"][0] for b in bs)
            text = (
                f"{side.upper()} IV is stable across {len(strikes)} strikes "
                f"({strikes[0]:,}–{strikes[-1]:,}) — writers are holding "
                f"premiums steady throughout this side of the chain."
            )
            consolidated.append(_bullet(
                text, category=CAT_REGIME, severity=SEVERITY_MODERATE,
                strikes=strikes, sides=[side], metrics=["iv"],
            ))
        else:
            consolidated.extend(bs)
    return consolidated + other


# ────────────────────────────────────────────────────────────────────────────
# Compose all templates with ordering by importance.
# Importance: DIVERG (strong) > WALL > SPOT > VOLUME > REGIME > CLUSTER
# ────────────────────────────────────────────────────────────────────────────
_CATEGORY_ORDER = {
    CAT_DIVERG:  0,
    CAT_WALL:    1,
    CAT_SPOT:    2,
    CAT_VOLUME:  3,
    CAT_REGIME:  4,
    CAT_CLUSTER: 5,
}
_SEVERITY_ORDER = {
    SEVERITY_STRONG:   0,
    SEVERITY_MODERATE: 1,
    SEVERITY_MILD:     2,
}


def generate(payload: dict) -> list[dict]:
    """
    Top-level entry. Takes the same dict returned by /analyzer/series and
    returns an ordered list of narrative bullets.
    """
    strikes      = payload.get("strikes", []) or []
    spot         = float(payload.get("spot") or 0)
    spot_series  = payload.get("spot_series", {}) or {}

    if not strikes or not spot:
        return []

    walls    = template_walls(strikes, spot)
    actions  = template_strike_actions(strikes)
    iv_regs  = template_iv_regime(strikes)
    volumes  = template_volume_spikes(strikes)
    rejects  = template_spot_rejections(strikes, spot_series, spot)
    divergs  = template_divergences(strikes, spot_series)
    chain    = template_chain_regime(strikes)

    # Consolidate redundant bullets BEFORE merging — collapse near-identical
    # rows so the user isn't reading the same observation 7 times.
    actions = cluster_adjacent_actions(actions)
    iv_regs = consolidate_iv_regime(iv_regs)

    bullets = walls + actions + iv_regs + volumes + rejects + divergs + chain

    # Order: most actionable first
    bullets.sort(key=lambda b: (
        _CATEGORY_ORDER.get(b["category"], 99),
        _SEVERITY_ORDER.get(b["severity"], 99),
    ))
    return bullets
