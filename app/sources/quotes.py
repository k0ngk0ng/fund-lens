from __future__ import annotations

import httpx

# 主用实时行情；push2 偶发限流/返空时回退 push2delay（约 3 分钟延迟，对估值无影响）。
HOSTS = [
    "https://push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
PATH = "/api/qt/ulist.np/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_CHUNK = 200


def _num(v):
    if v in ("-", "", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _get_diff(client, params):
    for host in HOSTS:
        try:
            r = await client.get(host + PATH, params=params, headers=HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
            diff = data.get("diff") or []
            if diff:
                return diff
        except Exception:
            continue
    return []


async def fetch_quotes(secids, client):
    """批量拉实时行情。返回 {股票代码: {price, change_pct, name, market}}。"""
    out = {}
    if not secids:
        return out
    for i in range(0, len(secids), _CHUNK):
        chunk = secids[i:i + _CHUNK]
        params = {"secids": ",".join(chunk), "fields": "f2,f3,f12,f13,f14"}
        for it in await _get_diff(client, params):
            code = str(it.get("f12"))
            price = _num(it.get("f2"))
            chg = _num(it.get("f3"))
            out[code] = {
                "price": price / 100 if price is not None else None,
                "change_pct": chg / 100 if chg is not None else None,
                "name": it.get("f14"),
                "market": it.get("f13"),
            }
    return out
