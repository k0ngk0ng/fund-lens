from __future__ import annotations

import asyncio
import re

import httpx

from ..market import guess_market_prefix
from ..models import Holding

F10 = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)

_ROW_CODE = re.compile(r"unify/r/(\d)\.(\w{5,6})")
_ROW_NAME = re.compile(r"class='tol'>\s*<a[^>]*>([^<]+)</a>")
_ROW_WEIGHT = re.compile(r"<td class='tor'>([\d.]+)%</td>")
_REPORT = re.compile(r"截止至：<font[^>]*>([\d-]+)</font>")


async def fetch_holdings_eastmoney(code, client):
    """直连东财 F10 解析前十大重仓股。href 里直接带 secid 前缀，无需猜市场。"""
    params = {"type": "jjcc", "code": code, "topline": "10"}
    r = await client.get(F10, params=params, headers=HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    text = r.text
    m = _REPORT.search(text)
    report_date = m.group(1) if m else ""
    t0 = text.find("<table")
    t1 = text.find("</table>", t0)
    table = text[t0:t1] if t0 != -1 else text
    holdings = []
    for tr in table.split("<tr>")[1:]:
        mc = _ROW_CODE.search(tr)
        mw = _ROW_WEIGHT.search(tr)
        if not (mc and mw):
            continue
        prefix, scode = mc.group(1), mc.group(2)
        mn = _ROW_NAME.search(tr)
        name = mn.group(1).strip() if mn else scode
        holdings.append(Holding(code=scode, name=name, market="A",
                                market_prefix=prefix, weight=float(mw.group(1))))
        if len(holdings) >= 10:
            break
    return report_date, holdings


def fetch_holdings_akshare(code):
    """用 AKShare 拿持仓（同步，引擎里以 to_thread 调用）。取最新报告期前十大。"""
    import akshare as ak
    from datetime import datetime

    year = datetime.now().year
    df = ak.fund_portfolio_hold_em(symbol=code, date=str(year))
    if df is None or len(df) == 0:
        df = ak.fund_portfolio_hold_em(symbol=code, date=str(year - 1))
    if df is None or len(df) == 0:
        return "", []
    if "季度" in df.columns:
        df = df[df["季度"] == df["季度"].iloc[0]]
    df = df.head(10)
    holdings = []
    for _, row in df.iterrows():
        scode = str(row.get("股票代码")).zfill(6)
        name = str(row.get("股票名称"))
        try:
            weight = float(row.get("占净值比例"))
        except (TypeError, ValueError):
            weight = 0.0
        holdings.append(Holding(code=scode, name=name, market="A",
                                market_prefix=guess_market_prefix(scode), weight=weight))
    return "", holdings


async def fetch_holdings(code, client, source="auto"):
    """返回 (report_date, [Holding])。auto: 先 AKShare，失败回退东财直连。"""
    if source in ("auto", "akshare"):
        try:
            rd, hs = await asyncio.to_thread(fetch_holdings_akshare, code)
            if hs:
                return rd, hs
        except Exception:
            if source == "akshare":
                raise
    return await fetch_holdings_eastmoney(code, client)
