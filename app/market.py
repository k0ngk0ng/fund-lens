from __future__ import annotations

from datetime import datetime, time

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _TZ = None


def now_sh():
    return datetime.now(_TZ) if _TZ else datetime.now()


def guess_market_prefix(code):
    """A股股票代码 -> push2 secid 前缀。沪市 1，深市/北交所 0。"""
    code = str(code)
    return "1" if code[:1] in ("5", "6", "9") else "0"


# 东财 push2 市场前缀 -> 显示标签（仅供展示，估值与标签无关）。
_MARKET_MAP = {
    "1": "A", "0": "A",                       # 沪 / 深·北
    "116": "HK", "128": "HK",                 # 港股
    "105": "US", "106": "US", "107": "US",    # 美股 NASDAQ/NYSE/AMEX
    "153": "ADR",                             # 粉单 / ADR
    "155": "UK",                              # 英股 / 欧股
}


def market_from_prefix(prefix):
    """push2 secid 前缀 -> 市场标签。未知前缀归为"海外"（仍可正常取价估值）。"""
    return _MARKET_MAP.get(str(prefix), "海外")


def is_a_share_open(dt=None):
    dt = dt or now_sh()
    if dt.weekday() >= 5:  # 周末（节假日 v1 暂不处理）
        return False
    t = dt.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))
