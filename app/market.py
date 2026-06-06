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


def is_a_share_open(dt=None):
    dt = dt or now_sh()
    if dt.weekday() >= 5:  # 周末（节假日 v1 暂不处理）
        return False
    t = dt.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))
