from __future__ import annotations

import asyncio

import httpx

from .config import Config
from .market import is_a_share_open, now_sh
from .models import FundSnapshot
from .sources.holdings import fetch_holdings
from .sources.official import fetch_official
from .sources.quotes import fetch_after_hours, fetch_quotes

_US_PREFIXES = ("105", "106", "107")  # 美股 NASDAQ/NYSE/AMEX，有盘前盘后


def _us_secids(hs):
    return [h.secid for h in hs if h.market_prefix in _US_PREFIXES]


class Engine:
    """对全目录池统一计算估值快照；按用户关注列表过滤后下发。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = None
        self.db = None            # 共享 aiosqlite 连接，由 lifespan 注入
        self.holdings = {}        # code -> (report_date, [Holding])
        self.snapshots = {}       # code -> FundSnapshot
        self.market_open = False
        self.server_time = ""
        self._subscribers = set()
        self._task = None
        self._holdings_day = ""
        self._stop = asyncio.Event()

    async def start(self):
        self.client = httpx.AsyncClient(follow_redirects=True)
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
        if self.client:
            await self.client.aclose()

    # ---- SSE pub/sub ----
    def subscribe(self):
        q = asyncio.Queue(maxsize=4)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)

    def _broadcast(self):
        for q in list(self._subscribers):
            try:
                q.put_nowait(True)
            except asyncio.QueueFull:
                pass

    # ---- data ----
    async def current_codes(self):
        """需要计算的基金 = 所有用户关注的并集 + 配置种子池。"""
        codes = list(self.cfg.funds)
        if self.db is not None:
            try:
                cur = await self.db.execute("SELECT DISTINCT fund_code FROM user_funds")
                for r in await cur.fetchall():
                    c = r["fund_code"]
                    if c not in codes:
                        codes.append(c)
            except Exception:
                pass
        return codes

    async def ensure_holdings(self, codes):
        """每日全量刷新一次持仓；新加入的基金即时补拉。"""
        today = now_sh().strftime("%Y-%m-%d")
        full_refresh = self._holdings_day != today
        for code in codes:
            if not full_refresh and code in self.holdings:
                continue
            try:
                rd, hs = await fetch_holdings(code, self.client, self.cfg.holdings_source)
                if hs:
                    self.holdings[code] = (rd, hs)
            except Exception:
                pass  # 保留上次持仓
        if full_refresh:
            self._holdings_day = today

    async def refresh_once(self):
        codes = await self.current_codes()
        await self.ensure_holdings(codes)

        secids = []
        seen = set()
        for code in codes:
            for h in self.holdings.get(code, ("", []))[1]:
                if h.secid not in seen:
                    seen.add(h.secid)
                    secids.append(h.secid)
        try:
            quotes = await fetch_quotes(secids, self.client)
        except Exception:
            quotes = {}

        us_secids = [s for s in secids if s.split(".")[0] in _US_PREFIXES]
        try:
            after_hours = await fetch_after_hours(us_secids, self.client)
        except Exception:
            after_hours = {}

        now_iso = now_sh().strftime("%Y-%m-%d %H:%M:%S")
        await asyncio.gather(*(self._compute_fund(c, quotes, now_iso, after_hours)
                               for c in codes))

        self.market_open = is_a_share_open()
        self.server_time = now_iso
        self._broadcast()

    async def ensure_fund(self, code):
        """用户新增基金后即时补拉持仓并算一次，立刻可见（无需等下一轮）。"""
        if code not in self.holdings:
            try:
                rd, hs = await fetch_holdings(code, self.client, self.cfg.holdings_source)
                if hs:
                    self.holdings[code] = (rd, hs)
            except Exception:
                pass
        hs = self.holdings.get(code, ("", []))[1]
        try:
            quotes = await fetch_quotes([h.secid for h in hs], self.client)
        except Exception:
            quotes = {}
        try:
            after_hours = await fetch_after_hours(_us_secids(hs), self.client)
        except Exception:
            after_hours = {}
        now_iso = now_sh().strftime("%Y-%m-%d %H:%M:%S")
        await self._compute_fund(code, quotes, now_iso, after_hours)
        self._broadcast()

    async def _compute_fund(self, code, quotes, now_iso, after_hours=None):
        after_hours = after_hours or {}
        rd, hs = self.holdings.get(code, ("", []))
        snap = self.snapshots.get(code) or FundSnapshot(code=code)
        snap.report_date = rd

        num = den = cov = 0.0
        for h in hs:
            q = quotes.get(h.code)
            if q:
                h.price = q.get("price")
                h.change_pct = q.get("change_pct")
            ah = after_hours.get(h.code)
            if ah:
                h.ah_price = ah.get("ah_price")
                h.ah_change_pct = ah.get("ah_change_pct")
            cov += h.weight
            if h.change_pct is not None:
                num += h.weight * h.change_pct
                den += h.weight
        snap.holdings = hs
        snap.coverage = round(cov, 2)

        if den > 0:
            if self.cfg.valuation_method == "raw":
                snap.est_pct = round(num / 100.0, 4)
            else:
                snap.est_pct = round(num / den, 4)
        else:
            snap.est_pct = None

        try:
            off = await fetch_official(code, self.client)
        except Exception:
            off = None
        if off:
            if off.get("name"):
                snap.name = off["name"]
            if off.get("prev_nav") is not None:
                snap.prev_nav = off["prev_nav"]
            snap.official_pct = off.get("official_pct")
            snap.official_nav = off.get("official_nav")
            snap.official_time = off.get("official_time", "")

        if snap.prev_nav is not None and snap.est_pct is not None:
            snap.est_nav = round(snap.prev_nav * (1 + snap.est_pct / 100.0), 4)
        else:
            snap.est_nav = None

        snap.updated_at = now_iso
        snap.status = "ok" if (snap.est_pct is not None or snap.official_pct is not None) else "stale"
        self.snapshots[code] = snap

    async def _loop(self):
        while not self._stop.is_set():
            try:
                await self.refresh_once()
            except Exception:
                pass
            interval = self.cfg.refresh_interval if is_a_share_open() else self.cfg.idle_interval
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def snapshot_for(self, codes):
        funds = []
        for c in codes:
            s = self.snapshots.get(c)
            funds.append(s.to_dict() if s else
                         {"code": c, "name": "", "status": "pending", "holdings": []})
        return {
            "funds": funds,
            "market_open": self.market_open,
            "server_time": self.server_time,
        }
