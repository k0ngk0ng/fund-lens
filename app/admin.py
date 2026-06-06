"""用户管理命令（明文进、内部 hash，无需自己算 hash）。

用法:
  python -m app.admin add <用户名> <密码>
  python -m app.admin passwd <用户名> <新密码>
  python -m app.admin delete <用户名>
  python -m app.admin list
"""
from __future__ import annotations

import argparse
import asyncio

from . import db as dbm
from .config import load_config


async def _run(args):
    cfg = load_config()
    await dbm.init_db(cfg.db_path)
    db = await dbm.connect(cfg.db_path)
    try:
        if args.cmd == "add":
            if await dbm.get_user_by_name(db, args.username):
                print(f"用户已存在: {args.username}")
                return
            await dbm.create_user(db, args.username, args.password)
            print(f"已创建用户: {args.username}")
        elif args.cmd == "passwd":
            n = await dbm.set_password(db, args.username, args.password)
            print("已修改密码" if n else "用户不存在")
        elif args.cmd == "delete":
            n = await dbm.delete_user(db, args.username)
            print("已删除" if n else "用户不存在")
        elif args.cmd == "list":
            rows = await dbm.list_users(db)
            if not rows:
                print("(无用户)")
            for r in rows:
                print(f"{r['id']}\t{r['username']}\tactive={r['is_active']}\t{r['created_at']}")
    finally:
        await db.close()


def main():
    p = argparse.ArgumentParser(prog="app.admin", description="Fund Lens 用户管理")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="新增用户")
    a.add_argument("username")
    a.add_argument("password")
    pw = sub.add_parser("passwd", help="修改密码")
    pw.add_argument("username")
    pw.add_argument("password")
    d = sub.add_parser("delete", help="删除用户")
    d.add_argument("username")
    sub.add_parser("list", help="列出用户")
    args = p.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
