"""Unit tests for signal_engine.py — stateless computation + dedup logic."""
import pytest
import signal_engine
from signal_engine import (
    generate_signal,
    _collect_counter_reasons,
    _round_premium,
    _get_entry,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _health(score):
    return {"score": score}

def _linear(score):
    return {"score": score}

def _vel(v_type):
    return {"type": v_type}

def _oi_snap(spot, strikes, ce_ltps, pe_ltps):
    rows = [
        {"strike": s, "ce_ltp": ce, "pe_ltp": pe}
        for s, ce, pe in zip(strikes, ce_ltps, pe_ltps)
    ]
    return {"ultp": spot, "atm_strike": strikes[len(strikes) // 2], "rows": rows}


def _reset_state():
    """Clear module-level signal state between tests."""
    signal_engine._signal_state.clear()


# ── _round_premium ────────────────────────────────────────────────────────────

class TestRoundPremium:
    @pytest.mark.parametrize("val,expected", [
        (10.0,  10.0),
        (10.3,  10.5),
        (10.24, 10.0),
        (10.75, 11.0),
        (0.1,   0.0),
        (0.26,  0.5),
    ])
    def test_rounds_to_nearest_half(self, val, expected):
        assert _round_premium(val) == expected


# ── _collect_counter_reasons ──────────────────────────────────────────────────

class TestCollectCounterReasons:
    def test_clean_impulse_returns_empty(self):
        reasons = _collect_counter_reasons(
            regime="IMPULSE_UP", phase="TREND_RIDE",
            health_score=70, lin_score=70, vel_type="UP"
        )
        assert reasons == []

    def test_exhaustion_phase_triggers_counter(self):
        reasons = _collect_counter_reasons(
            regime="IMPULSE_UP", phase="EXHAUSTION",
            health_score=70, lin_score=70, vel_type="UP"
        )
        assert any("EXHAUSTION" in r for r in reasons)

    def test_reversal_regime_triggers_counter(self):
        reasons = _collect_counter_reasons(
            regime="REVERSAL_WATCH", phase="TREND_RIDE",
            health_score=70, lin_score=70, vel_type="UP"
        )
        assert any("crossover" in r.lower() or "reversal" in r.lower() for r in reasons)

    def test_low_health_triggers_counter(self):
        reasons = _collect_counter_reasons(
            regime="IMPULSE_UP", phase="TREND_RIDE",
            health_score=30, lin_score=70, vel_type="UP"
        )
        assert any("health" in r.lower() for r in reasons)

    def test_low_lin_score_triggers_counter(self):
        reasons = _collect_counter_reasons(
            regime="IMPULSE_UP", phase="TREND_RIDE",
            health_score=70, lin_score=30, vel_type="UP"
        )
        assert any("linear" in r.lower() or "score" in r.lower() for r in reasons)

    def test_flat_velocity_on_impulse_triggers_counter(self):
        reasons = _collect_counter_reasons(
            regime="IMPULSE_UP", phase="TREND_RIDE",
            health_score=70, lin_score=70, vel_type="FLAT"
        )
        assert any("velocity" in r.lower() or "momentum" in r.lower() for r in reasons)

    def test_consolidation_without_breakout_triggers_counter(self):
        reasons = _collect_counter_reasons(
            regime="CONSOLIDATION", phase="TREND_RIDE",
            health_score=70, lin_score=70, vel_type="UP"
        )
        assert any("consolidation" in r.lower() for r in reasons)


# ── generate_signal (action routing) ─────────────────────────────────────────

class TestGenerateSignal:
    def setup_method(self):
        _reset_state()

    def _snap(self):
        return _oi_snap(
            spot=23000,
            strikes=[22900, 23000, 23100],
            ce_ltps=[120.0, 85.0, 55.0],
            pe_ltps=[50.0,  85.0, 120.0],
        )

    def test_no_trade_when_counter_signals_active(self):
        sig = generate_signal(
            instrument="NIFTY",
            regime="REVERSAL_WATCH",
            phase="EXHAUSTION",
            health=_health(25),
            linear_score=_linear(25),
            velocity=_vel("FLAT"),
            oi_snap=None,
            spot=23000,
        )
        assert sig["action"] == "NO_TRADE"
        assert len(sig["counter_reasons"]) > 0

    def test_buy_ce_on_impulse_up(self):
        sig = generate_signal(
            instrument="NIFTY",
            regime="IMPULSE_UP",
            phase="BREAKOUT",
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("UP"),
            oi_snap=self._snap(),
            spot=23000,
        )
        assert sig["action"] == "BUY"
        assert sig["direction"] == "CE"

    def test_buy_pe_on_impulse_down(self):
        sig = generate_signal(
            instrument="BANKNIFTY",
            regime="IMPULSE_DOWN",
            phase="TREND_RIDE",
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("DOWN"),
            oi_snap=self._snap(),
            spot=23000,
        )
        assert sig["action"] == "BUY"
        assert sig["direction"] == "PE"

    def test_wait_when_scores_below_threshold(self):
        sig = generate_signal(
            instrument="NIFTY",
            regime="IMPULSE_UP",
            phase="BREAKOUT",
            health=_health(45),     # below _MIN_HEALTH_SCORE (50)
            linear_score=_linear(60),  # below _MIN_LINEAR_SCORE (65)
            velocity=_vel("UP"),
            oi_snap=None,
            spot=23000,
        )
        assert sig["action"] == "WAIT"

    def test_wait_when_phase_not_in_triggers(self):
        sig = generate_signal(
            instrument="NIFTY",
            regime="IMPULSE_UP",
            phase="ACCUMULATION",
            health=_health(80),
            linear_score=_linear(80),
            velocity=_vel("UP"),
            oi_snap=None,
            spot=23000,
        )
        assert sig["action"] == "WAIT"

    def test_buy_signal_has_entry_target_sl(self):
        sig = generate_signal(
            instrument="NIFTY",
            regime="IMPULSE_UP",
            phase="BREAKOUT",
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("UP"),
            oi_snap=self._snap(),
            spot=23000,
        )
        if sig["action"] == "BUY":
            assert sig["entry"] is not None
            assert sig["target"] is not None
            assert sig["sl"] is not None
            # 2:1 R:R: target > entry > sl
            assert sig["target"] > sig["entry"] > sig["sl"]

    def test_result_always_has_required_keys(self):
        sig = generate_signal(
            instrument="NIFTY",
            regime="IMPULSE_UP",
            phase="BREAKOUT",
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("UP"),
            oi_snap=None,
            spot=None,
        )
        required = {"action", "direction", "instrument", "atm_strike", "entry",
                    "target", "sl", "reason", "counter_reasons", "regime", "phase",
                    "health_score", "lin_score", "generated_at", "is_new"}
        assert required.issubset(sig.keys())

    def test_first_signal_is_new(self):
        sig = generate_signal(
            instrument="FRESH_INSTRUMENT",
            regime="IMPULSE_UP",
            phase="BREAKOUT",
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("UP"),
            oi_snap=self._snap(),
            spot=23000,
        )
        assert sig["is_new"] is True

    def test_repeated_same_action_is_not_new(self):
        kwargs = dict(
            instrument="NIFTY_DEDUP",
            regime="IMPULSE_UP",
            phase="BREAKOUT",
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("UP"),
            oi_snap=self._snap(),
            spot=23000,
        )
        first  = generate_signal(**kwargs)
        second = generate_signal(**kwargs)
        assert first["is_new"] is True
        assert second["is_new"] is False

    def test_action_change_produces_new_signal(self):
        # First: WAIT
        wait = generate_signal(
            instrument="NIFTY_CHANGE",
            regime="IMPULSE_UP",
            phase="ACCUMULATION",     # not in _BUY_PHASE_TRIGGERS → WAIT
            health=_health(70),
            linear_score=_linear(70),
            velocity=_vel("UP"),
            oi_snap=None,
            spot=23000,
        )
        # Then: NO_TRADE
        no_trade = generate_signal(
            instrument="NIFTY_CHANGE",
            regime="REVERSAL_WATCH",
            phase="EXHAUSTION",
            health=_health(20),
            linear_score=_linear(20),
            velocity=_vel("FLAT"),
            oi_snap=None,
            spot=23000,
        )
        assert wait["action"] == "WAIT"
        assert no_trade["action"] == "NO_TRADE"
        assert no_trade["is_new"] is True
