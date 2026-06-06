from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Config:
    refresh_interval: int = 10
    idle_interval: int = 300
    valuation_method: str = "normalized"   # normalized | raw
    color_scheme: str = "cn"               # cn | intl
    holdings_source: str = "auto"          # auto | akshare | eastmoney
    markets: dict = field(default_factory=lambda: {"a_share": True, "hk": False, "us": False})
    funds: list = field(default_factory=list)
    db_path: str = "data/fundlens.db"
    secret_key: str = "dev-insecure-secret-change-me"
    admin_user: str = ""
    admin_password: str = ""


def _load_dotenv():
    """轻量加载项目根 .env（不覆盖已存在的真实环境变量）。
    docker/systemd 已通过 env_file/EnvironmentFile 注入，这里主要方便
    本地直接跑 uvicorn 或 `python -m app.admin`。"""
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


def _int_env(name, default):
    v = os.getenv(name)
    if not v or not v.strip():
        return default
    try:
        return int(v)
    except ValueError:
        return default


def load_config(path=None):
    _load_dotenv()
    path = path or os.getenv("CONFIG_PATH", "funds.yaml")
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    funds = [str(c).strip() for c in (data.get("funds") or []) if str(c).strip()]
    markets = data.get("markets") or {}

    return Config(
        refresh_interval=_int_env("REFRESH_INTERVAL", int(data.get("refresh_interval_seconds", 10))),
        idle_interval=_int_env("IDLE_INTERVAL", int(data.get("idle_interval_seconds", 300))),
        valuation_method=os.getenv("VALUATION_METHOD", data.get("valuation_method", "normalized")),
        color_scheme=data.get("color_scheme", "cn"),
        holdings_source=os.getenv("HOLDINGS_SOURCE", data.get("holdings_source", "auto")),
        markets={
            "a_share": markets.get("a_share", True),
            "hk": markets.get("hk", False),
            "us": markets.get("us", False),
        },
        funds=funds,
        db_path=os.getenv("DB_PATH", "data/fundlens.db"),
        secret_key=os.getenv("SECRET_KEY", "dev-insecure-secret-change-me"),
        admin_user=os.getenv("ADMIN_USER", ""),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
    )
