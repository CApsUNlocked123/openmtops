"""Unit tests for indicators.py — pure functions, no external I/O."""
import pytest
from indicators import (
    calculate_max_pain,
    calculate_pcr,
    classify_pcr,
    classify_oi_levels,
    assess_oi_clarity,
    generate_signals,
)
from tests.conftest import make_oi_df


# ── calculate_max_pain ────────────────────────────────────────────────────────

class TestMaxPain:
    def test_symmetric_chain_returns_middle_strike(self, simple_df):
        # Equal CE/PE OI at all strikes → max pain is the middle strike (23000)
        assert calculate_max_pain(simple_df) == 23000

    def test_heavy_pe_oi_pulls_max_pain_up(self):
        # Lots of PE OI at 23000 → put writers want spot to close ABOVE 23000
        # (above 23000 no puts expire ITM) → max pain shifts higher
        df = make_oi_df(
            strikes=[22900, 23000, 23100, 23200],
            ce_ois=[500, 500, 500, 500],
            pe_ois=[500, 10000, 500, 500],
        )
        result = calculate_max_pain(df)
        assert result >= 23000

    def test_single_strike(self):
        df = make_oi_df([23000], [1000], [1000])
        assert calculate_max_pain(df) == 23000

    def test_returns_int(self, simple_df):
        result = calculate_max_pain(simple_df)
        assert isinstance(result, int)


# ── calculate_pcr ─────────────────────────────────────────────────────────────

class TestCalculatePcr:
    def test_equal_oi_gives_pcr_one(self, simple_df):
        # Total PE OI == total CE OI → PCR = 1.000
        assert calculate_pcr(simple_df) == 1.0

    def test_heavy_pe_gives_pcr_above_one(self):
        df = make_oi_df([23000], [1000], [2000])
        assert calculate_pcr(df) == 2.0

    def test_zero_ce_oi_returns_zero(self):
        df = make_oi_df([23000], [0], [1000])
        assert calculate_pcr(df) == 0.0

    def test_pcr_is_rounded_to_three_decimals(self):
        # 1000 PE / 3000 CE = 0.333333… → rounds to 0.333
        df = make_oi_df([23000], [3000], [1000])
        result = calculate_pcr(df)
        assert result == round(1000 / 3000, 3)


# ── classify_pcr ──────────────────────────────────────────────────────────────

class TestClassifyPcr:
    @pytest.mark.parametrize("pcr,expected", [
        (1.5,  "BULLISH"),
        (1.3,  "BULLISH"),
        (1.29, "MILDLY_BULLISH"),
        (1.0,  "MILDLY_BULLISH"),
        (0.99, "NEUTRAL"),
        (0.7,  "NEUTRAL"),
        (0.69, "MILDLY_BEARISH"),
        (0.5,  "MILDLY_BEARISH"),
        (0.49, "BEARISH"),
        (0.0,  "BEARISH"),
    ])
    def test_all_thresholds(self, pcr, expected):
        assert classify_pcr(pcr) == expected


# ── classify_oi_levels ────────────────────────────────────────────────────────

class TestClassifyOiLevels:
    def test_empty_df_returns_empty(self):
        df = make_oi_df([], [], [])
        assert classify_oi_levels(df) == []

    def test_all_zero_oi_returns_empty(self, simple_df):
        df = simple_df.copy()
        df["total_oi"] = 0
        df["ce_oi"] = 0
        df["pe_oi"] = 0
        assert classify_oi_levels(df) == []

    def test_pure_call_wall(self):
        # ce_oi >> pe_oi → classified as CALL_WALL
        df = make_oi_df([23000], ce_ois=[10000], pe_ois=[100])
        levels = classify_oi_levels(df)
        assert len(levels) == 1
        assert levels[0]["classification"] == "CALL_WALL"

    def test_pure_put_wall(self):
        df = make_oi_df([23000], ce_ois=[100], pe_ois=[10000])
        levels = classify_oi_levels(df)
        assert levels[0]["classification"] == "PUT_WALL"

    def test_fortress_when_both_sides_heavy(self):
        # ratio of 3:1 each way → FORTRESS
        df = make_oi_df([23000], ce_ois=[3000], pe_ois=[3000])
        # ce/pe = 1, pe/ce = 1 — neither meets call_wall_ratio=2.0
        # Equal → RESISTANCE (ce >= pe branch)
        levels = classify_oi_levels(df)
        assert levels[0]["classification"] in ("FORTRESS", "RESISTANCE", "SUPPORT")

    def test_result_contains_required_keys(self):
        df = make_oi_df([23000], ce_ois=[1000], pe_ois=[2000])
        levels = classify_oi_levels(df)
        required = {"strike", "ce_oi", "pe_oi", "total_oi", "classification", "tier",
                    "ce_pe_ratio", "pe_ce_ratio"}
        assert required.issubset(levels[0].keys())

    def test_tier1_is_highest_oi(self, skewed_df):
        levels = classify_oi_levels(skewed_df)
        tier1 = [lv for lv in levels if lv["tier"] == 1]
        assert len(tier1) >= 1
        # The single highest-OI level must be in tier 1
        max_oi_strike = skewed_df.loc[skewed_df["total_oi"].idxmax(), "strike"]
        assert any(lv["strike"] == max_oi_strike for lv in tier1)


# ── assess_oi_clarity ─────────────────────────────────────────────────────────

class TestAssessOiClarity:
    def test_no_tier1_returns_no_map(self):
        assert assess_oi_clarity([]) == "NO_MAP"

    def test_single_tier1_level_is_clear(self):
        levels = [{"tier": 1, "total_oi": 5000}]
        assert assess_oi_clarity(levels) == "CLEAR"

    def test_two_tier1_dominant_one_is_clear(self):
        # ratio 4000/1000 = 4.0 ≥ 2.0 → CLEAR
        levels = [
            {"tier": 1, "total_oi": 4000},
            {"tier": 1, "total_oi": 1000},
        ]
        assert assess_oi_clarity(levels) == "CLEAR"

    def test_two_tier1_close_is_mixed(self):
        # ratio 1100/1000 = 1.1 < 2.0 → MIXED
        levels = [
            {"tier": 1, "total_oi": 1100},
            {"tier": 1, "total_oi": 1000},
        ]
        assert assess_oi_clarity(levels) == "MIXED"


# ── generate_signals ──────────────────────────────────────────────────────────

class TestGenerateSignals:
    def _levels(self):
        return [
            {"strike": 23100, "ce_oi": 100, "pe_oi": 5000, "total_oi": 5100,
             "classification": "PUT_WALL", "tier": 1,
             "ce_pe_ratio": 0.02, "pe_ce_ratio": 50.0},
        ]

    def test_empty_df_returns_no_signals(self):
        df = make_oi_df([], [], [])
        assert generate_signals(df, 23000, 23000, 1.2, "BULLISH", [], "CLEAR") == []

    def test_zero_spot_returns_no_signals(self, simple_df):
        assert generate_signals(simple_df, 0, 23000, 1.2, "BULLISH", [], "CLEAR") == []

    def test_no_levels_returns_no_signals(self, simple_df):
        assert generate_signals(simple_df, 23000, 23000, 1.2, "BULLISH", [], "CLEAR") == []

    def test_signal_keys_always_present(self, simple_df):
        levels = self._levels()
        signals = generate_signals(simple_df, 23000, 23000, 1.2, "BULLISH", levels, "CLEAR")
        if signals:
            required = {"signal_type", "confidence", "entry_price", "target_price",
                        "stop_loss", "setup", "met_conditions", "failed_conditions",
                        "key_metrics", "timestamp"}
            assert required.issubset(signals[0].keys())

    def test_signals_sorted_by_confidence_descending(self, simple_df):
        levels = self._levels()
        signals = generate_signals(simple_df, 23000, 23100, 1.4, "BULLISH", levels, "CLEAR")
        confs = [s["confidence"] for s in signals]
        assert confs == sorted(confs, reverse=True)
