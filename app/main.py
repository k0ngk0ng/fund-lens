from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db as dbm
from .config import load_config
from .engine import Engine
from .sources.holdings import fetch_holdings
from .sources.official import fetch_official

_CODE_RE = re.compile(r"\d{6}")

cfg = load_config()
engine = Engine(cfg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await dbm.init_db(cfg.db_path)
    app.state.db = await dbm.connect(cfg.db_path)
    engine.db = app.state.db                     # 引擎按"用户关注并集"动态计算
    for code in cfg.funds:                       # 种子目录池
        await dbm.upsert_fund(app.state.db, code)
    if cfg.admin_user and cfg.admin_password:    # 种子管理员（库空时）
        if await dbm.count_users(app.state.db) == 0:
            await dbm.create_user(app.state.db, cfg.admin_user, cfg.admin_password)
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()
        await app.state.db.close()


app = FastAPI(title="Fund Lens", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=cfg.secret_key,
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 24 * 14,
)


async def require_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    user = await dbm.get_user_by_id(request.app.state.db, uid)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="未登录")
    return user


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = await dbm.get_user_by_name(request.app.state.db, username)
    if not user or not dbm.verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    request.session["uid"] = user["id"]
    return {"username": user["username"]}


@app.post("/api/logout")
async def logout(request: Request, user=Depends(require_user)):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(user=Depends(require_user)):
    return {"username": user["username"]}


@app.get("/api/catalog")
async def catalog(request: Request, user=Depends(require_user)):
    rows = await dbm.list_catalog(request.app.state.db)
    out = []
    for r in rows:
        name = r["name"]
        if not name:
            s = engine.snapshots.get(r["code"])
            name = s.name if s else ""
        out.append({"code": r["code"], "name": name, "market": r["market"]})
    return {"funds": out}


@app.get("/api/watchlist")
async def get_watchlist(request: Request, user=Depends(require_user)):
    codes = await dbm.get_watchlist(request.app.state.db, user["id"])
    return {"codes": codes}


@app.put("/api/watchlist")
async def put_watchlist(request: Request, user=Depends(require_user)):
    body = await request.json()
    wanted = [str(c).strip() for c in (body.get("codes") or []) if str(c).strip()]
    catalog_codes = {r["code"] for r in await dbm.list_catalog(request.app.state.db)}
    codes = [c for c in wanted if c in catalog_codes]
    await dbm.set_watchlist(request.app.state.db, user["id"], codes)
    return {"codes": codes}


@app.post("/api/watchlist/add")
async def add_fund(request: Request, user=Depends(require_user)):
    """用户自由添加任意基金：校验代码有效 -> 入目录 -> 加关注 -> 即时计算。"""
    body = await request.json()
    code = str(body.get("code") or "").strip()
    if not _CODE_RE.fullmatch(code):
        raise HTTPException(status_code=400, detail="请输入 6 位基金代码")

    db = request.app.state.db
    name = None
    try:
        off = await fetch_official(code, engine.client)
        if off and off.get("name"):
            name = off["name"]
    except Exception:
        pass
    if not name:  # 官方估值拿不到时，用能否取到持仓作为存在性兜底校验
        try:
            _rd, hs = await fetch_holdings(code, engine.client, cfg.holdings_source)
        except Exception:
            hs = []
        if not hs:
            raise HTTPException(status_code=404, detail="未找到该基金，请检查代码")

    await dbm.upsert_fund(db, code, name)
    codes = await dbm.get_watchlist(db, user["id"])
    if code not in codes:
        codes.append(code)
        await dbm.set_watchlist(db, user["id"], codes)
    await engine.ensure_fund(code)
    return {"code": code, "name": name or ""}


@app.get("/api/snapshot")
async def snapshot(request: Request, user=Depends(require_user)):
    codes = await dbm.get_watchlist(request.app.state.db, user["id"])
    return engine.snapshot_for(codes)


def _sse(data):
    return f"event: snapshot\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(request: Request, user=Depends(require_user)):
    uid = user["id"]
    db = request.app.state.db

    async def gen():
        q = engine.subscribe()
        try:
            codes = await dbm.get_watchlist(db, uid)
            yield _sse(engine.snapshot_for(codes))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(q.get(), timeout=15)
                    codes = await dbm.get_watchlist(db, uid)
                    yield _sse(engine.snapshot_for(codes))
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            engine.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/", StaticFiles(directory="static", html=True), name="static")
