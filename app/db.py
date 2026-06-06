from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS funds (
  code TEXT PRIMARY KEY,
  name TEXT,
  market TEXT DEFAULT 'A'
);
CREATE TABLE IF NOT EXISTS user_funds (
  user_id INTEGER NOT NULL,
  fund_code TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, fund_code)
);
"""

_PBKDF2_ITER = 200_000


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        _algo, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


async def init_db(db_path):
    d = os.path.dirname(db_path)
    if d:
        os.makedirs(d, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def connect(db_path):
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    return db


# ---- users ----
async def create_user(db, username, password):
    await db.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
        (username, hash_password(password), datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


async def set_password(db, username, password):
    cur = await db.execute("UPDATE users SET password_hash=? WHERE username=?",
                           (hash_password(password), username))
    await db.commit()
    return cur.rowcount


async def delete_user(db, username):
    cur = await db.execute("DELETE FROM users WHERE username=?", (username,))
    await db.commit()
    return cur.rowcount


async def list_users(db):
    cur = await db.execute("SELECT id, username, is_active, created_at FROM users ORDER BY id")
    return await cur.fetchall()


async def get_user_by_name(db, username):
    cur = await db.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,))
    return await cur.fetchone()


async def get_user_by_id(db, uid):
    cur = await db.execute("SELECT * FROM users WHERE id=? AND is_active=1", (uid,))
    return await cur.fetchone()


async def count_users(db):
    cur = await db.execute("SELECT COUNT(*) AS c FROM users")
    row = await cur.fetchone()
    return row["c"]


# ---- funds catalog ----
async def upsert_fund(db, code, name=None, market="A"):
    await db.execute(
        "INSERT INTO funds (code, name, market) VALUES (?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET name=COALESCE(excluded.name, funds.name)",
        (code, name, market),
    )
    await db.commit()


async def list_catalog(db):
    cur = await db.execute("SELECT code, name, market FROM funds ORDER BY code")
    return await cur.fetchall()


# ---- watchlist ----
async def get_watchlist(db, uid):
    cur = await db.execute(
        "SELECT fund_code FROM user_funds WHERE user_id=? ORDER BY sort_order, fund_code", (uid,)
    )
    return [r["fund_code"] for r in await cur.fetchall()]


async def set_watchlist(db, uid, codes):
    await db.execute("DELETE FROM user_funds WHERE user_id=?", (uid,))
    await db.executemany(
        "INSERT OR IGNORE INTO user_funds (user_id, fund_code, sort_order) VALUES (?,?,?)",
        [(uid, c, i) for i, c in enumerate(codes)],
    )
    await db.commit()
