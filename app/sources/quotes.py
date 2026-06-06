from __future__ import annotations

import asyncio

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

# 美股盘后/盘前：ulist.np 不返回盘后字段，只能逐只走 qt/stock/get（需 ut 令牌）。
# 仅展示，不参与估值。字段含义为推断（周末美股休市时 f520~f524 全为 0，无法验证）：
#   f520=盘后价、f522=盘后涨跌幅（×100，与 f43/f170 同量纲）。
# TODO(校验): 美股盘前/盘后实时时段（北京时间约周二~周六凌晨）对照
#   quote.eastmoney.com/us/AAPL.html 确认 f520/f522 映射后再视为可信。
_AH_PATH = "/api/qt/stock/get"
_AH_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_AH_REFERER = "https://quote.eastmoney.com/us/"
_AH_CONCURRENCY = 5


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


async def _ah_one(client, secid, sem):
    params = {"secid": secid, "fields": "f57,f520,f522", "ut": _AH_UT}
    headers = {**HEADERS, "Referer": _AH_REFERER}
    async with sem:
        for host in HOSTS:
            try:
                r = await client.get(host + _AH_PATH, params=params,
                                     headers=headers, timeout=_TIMEOUT)
                r.raise_for_status()
                d = (r.json() or {}).get("data") or {}
                code = str(d.get("f57") or "")
                price = _num(d.get("f520"))
                pct = _num(d.get("f522"))
                # 0 视为无盘后数据（休市/未开盘），不展示
                if code and price:
                    return code, {"ah_price": price / 100,
                                  "ah_change_pct": pct / 100 if pct is not None else None}
                return None
            except Exception:
                continue
    return None


async def fetch_after_hours(secids, client):
    """逐只拉美股盘后价（仅 105/106/107 等美股前缀）。返回 {代码: {ah_price, ah_change_pct}}。
    纯展示、best-effort：任何失败/休市都安静跳过，绝不影响估值。"""
    out = {}
    if not secids:
        return out
    sem = asyncio.Semaphore(_AH_CONCURRENCY)
    results = await asyncio.gather(*(_ah_one(client, s, sem) for s in secids))
    for res in results:
        if res:
            out[res[0]] = res[1]
    return out
