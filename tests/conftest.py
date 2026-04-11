"""Shared fixtures for unit tests."""
import pandas as pd
import pytest


def make_oi_df(strikes, ce_ois, pe_ois, ce_ltps=None, pe_ltps=None):
    """Build a minimal OI DataFrame for testing indicators."""
    n = len(strikes)
    df = pd.DataFrame({
        "strike":  strikes,
        "ce_oi":   ce_ois,
        "pe_oi":   pe_ois,
        "ce_ltp":  ce_ltps or [0.0] * n,
        "pe_ltp":  pe_ltps or [0.0] * n,
        "ce_iv":   [0.0] * n,
        "pe_iv":   [0.0] * n,
        "ce_delta": [0.0] * n,
        "pe_delta": [0.0] * n,
    })
    df["total_oi"] = df["ce_oi"] + df["pe_oi"]
    return df


@pytest.fixture
def simple_df():
    """5-strike OI chain used by multiple tests."""
    return make_oi_df(
        strikes=[22800, 22900, 23000, 23100, 23200],
        ce_ois  =[1000,  2000,  5000,  2000,  1000],
        pe_ois  =[1000,  2000,  5000,  2000,  1000],
    )


@pytest.fixture
def skewed_df():
    """Chain with heavy PE OI (bullish bias) and clear max pain."""
    return make_oi_df(
        strikes=[22800, 22900, 23000, 23100, 23200],
        ce_ois  =[  500,  1000,  2000,  4000,  8000],
        pe_ois  =[  500,  1000, 10000,  4000,   500],
    )
