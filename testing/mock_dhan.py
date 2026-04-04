"""
Mock dhan module — replaces dhan.py when TESTING=1.
Exposes the same public interface: dhan, dhan_context, lookup_security.
No network calls, no CSV download, no DhanHQ SDK required.
"""

from testing.dummy_data import SECURITY, FUND_LIMITS, ORDER_RESP, POSITIONS


class _MockDhanContext:
    pass


class _MockDhan:
    # Exchange / product / order-type constants (mirrors dhanhq.dhanhq)
    NSE_FNO = 2
    BSE_FNO = 3
    BUY     = "BUY"
    SELL    = "SELL"
    MARKET  = "MARKET"
    LIMIT   = "LIMIT"
    INTRA   = "INTRADAY"
    CNC     = "CNC"
    INDEX   = "IDX_I"

    def place_order(self, **kw):
        print(f"[MOCK dhan.place_order] {kw}")
        return dict(ORDER_RESP)

    def get_fund_limits(self):
        return dict(FUND_LIMITS)

    def get_positions(self):
        return dict(POSITIONS)

    def expiry_list(self, *a, **kw):
        return {"status": "success", "data": {"data": ["2025-05-29", "2025-06-26"]}}

    def option_chain(self, *a, **kw):
        # start_for_instrument in oi_tracker calls this; return error so it
        # falls through — dashboard gets OI from the TESTING shortcut instead.
        return {"status": "error", "remarks": "mock — OI served via TESTING shortcut"}


dhan_context = _MockDhanContext()
dhan         = _MockDhan()


def lookup_security(symbol: str, strike, option_type: str) -> dict | None:
    print(f"[MOCK lookup_security] {symbol} {strike} {option_type}")
    return dict(SECURITY)
