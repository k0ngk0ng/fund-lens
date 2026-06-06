from __future__ import annotations

import json

import httpx

URL = "https://fundgz.1234567.com.cn/js/{code}.js"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"}
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)


def _f(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def fetch_official(code, client):
    """天天基金官方盘中估值: jsonpgz({...})。用于拿昨日净值 + 官方估值对照。"""
    r = await client.get(URL.format(code=code), headers=HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    text = r.text.strip()
    s, e = text.find("("), text.rfind(")")
    if s == -1 or e == -1 or e <= s:
        return None
    obj = json.loads(text[s + 1:e])
    return {
        "name": obj.get("name"),
        "prev_nav": _f(obj.get("dwjz")),       # 昨日单位净值
        "official_pct": _f(obj.get("gszzl")),  # 官方估算涨跌幅 %
        "official_nav": _f(obj.get("gsz")),    # 官方估算净值
        "official_time": obj.get("gztime") or "",
        "jzrq": obj.get("jzrq") or "",
    }
