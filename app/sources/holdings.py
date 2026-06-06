from __future__ import annotations

import asyncio
import re

import httpx

from ..market import guess_market_prefix, market_from_prefix
from ..models import Holding

F10 = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)

# href 前缀可达 3 位(116=港股/105=美股)；股票代码含美股字母代码(如 AAPL)。
# A股行权重单元格用 class='tor'，QDII/海外行用 class='toc'，故权重正则放宽到任意 <td>。
# 每行内 unify/r 链接依次为：代码、名称、行情；取第二个的文本作为股票名称。
_ROW_CODE = re.compile(r"unify/r/(\d+)\.([A-Za-z0-9]{1,8})")
_ROW_LINKS = re.compile(r"unify/r/\d+\.[A-Za-z0-9]{1,8}'?\s*>([^<]+)</a>")
_ROW_WEIGHT = re.compile(r"<td[^>]*>([\d.]+)%</td>")
_REPORT = re.compile(r"截止至：<font[^>]*>([\d-]+)</font>")


def _parse_table(table):
    """从一个 <table> 里解析所有持仓行。"""
    holdings = []
    for tr in table.split("<tr>")[1:]:
        mc = _ROW_CODE.search(tr)
        mw = _ROW_WEIGHT.search(tr)
        if not (mc and mw):
            continue
        prefix, scode = mc.group(1), mc.group(2)
        texts = _ROW_LINKS.findall(tr)
        name = texts[1].strip() if len(texts) >= 2 else scode
        holdings.append(Holding(code=scode, name=name,
                                market=market_from_prefix(prefix),
                                market_prefix=prefix, weight=float(mw.group(1))))
    return holdings


def _parse_periods(text):
    """一次返回可能含多个报告期(boxitem)，逐个解析为 (report_date, [Holding])。"""
    out = []
    parts = _REPORT.split(text)            # [pre, date1, body1, date2, body2, ...]
    for i in range(1, len(parts), 2):
        rd = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        t0 = body.find("<table")
        t1 = body.find("</table>", t0)
        table = body[t0:t1] if t0 != -1 else ""
        hs = _parse_table(table)
        if hs:
            out.append((rd, hs))
    return out


async def fetch_holdings_eastmoney(code, client):
    """直连东财 F10 取【全部持仓】。href 带 secid 前缀，可识别 A股/港股/美股。

    主动型基金季报仅披露前十大，全部持仓只在半年报(MM-DD=06-30)/年报(12-31)披露。
    故统一回溯到最近一次半年报/年报，取其全部持仓；若两年内都没有，退而取持仓最多的一期。"""
    from datetime import datetime

    year = datetime.now().year
    periods = []
    for y in (year, year - 1):
        try:
            r = await client.get(F10, params={"type": "jjcc", "code": code,
                                              "year": str(y), "topline": "200"},
                                 headers=HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
            periods.extend(_parse_periods(r.text))
        except Exception:
            continue
    if not periods:
        return "", []
    full = [p for p in periods if p[0].endswith("-06-30") or p[0].endswith("-12-31")]
    if full:
        full.sort(key=lambda p: p[0])               # 按日期升序
        rd, holdings = full[-1]                       # 最近一次半年报/年报
    else:
        periods.sort(key=lambda p: (len(p[1]), p[0]))
        rd, holdings = periods[-1]                    # 兜底：持仓最多且最新
    return rd, holdings


def fetch_holdings_akshare(code):
    """用 AKShare 拿持仓（同步，引擎里以 to_thread 调用）。取最新报告期全部持仓。
    注意：AKShare 返回的代码不带市场前缀，按沪深规则推断，仅适用 A股；港/美股请走东财直连。"""
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
    """返回 (report_date, [Holding])。
    auto: 先东财直连(带市场前缀，支持港/美股 + 全持仓)，失败回退 AKShare(仅A股)。"""
    if source in ("auto", "eastmoney"):
        try:
            rd, hs = await fetch_holdings_eastmoney(code, client)
            if hs:
                return rd, hs
        except Exception:
            if source == "eastmoney":
                raise
    rd, hs = await asyncio.to_thread(fetch_holdings_akshare, code)
    return rd, hs
