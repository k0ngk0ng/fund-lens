from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Holding:
    code: str
    name: str
    market: str = "A"          # A | HK | US
    market_prefix: str = "1"   # secid 前缀: "1"=沪, "0"=深/北
    weight: float = 0.0        # 占净值比例 %
    price: float | None = None
    change_pct: float | None = None
    # 美股盘后/盘前（仅展示，不参与估值；QDII 净值按正常时段收盘结算）
    ah_price: float | None = None
    ah_change_pct: float | None = None

    @property
    def secid(self) -> str:
        return f"{self.market_prefix}.{self.code}"

    def to_dict(self):
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "weight": self.weight,
            "price": self.price,
            "change_pct": self.change_pct,
            "ah_price": self.ah_price,
            "ah_change_pct": self.ah_change_pct,
        }


@dataclass
class FundSnapshot:
    code: str
    name: str = ""
    prev_nav: float | None = None
    est_pct: float | None = None
    est_nav: float | None = None
    official_pct: float | None = None
    official_nav: float | None = None
    official_time: str = ""
    report_date: str = ""
    coverage: float = 0.0
    holdings: list = field(default_factory=list)
    updated_at: str = ""
    status: str = "ok"          # ok | stale | error | pending

    def to_dict(self):
        return {
            "code": self.code,
            "name": self.name,
            "prev_nav": self.prev_nav,
            "est_pct": self.est_pct,
            "est_nav": self.est_nav,
            "official_pct": self.official_pct,
            "official_nav": self.official_nav,
            "official_time": self.official_time,
            "report_date": self.report_date,
            "coverage": self.coverage,
            "holdings": [h.to_dict() for h in self.holdings],
            "updated_at": self.updated_at,
            "status": self.status,
        }
