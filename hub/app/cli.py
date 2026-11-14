"""Command line of the Hub: recovery operations that deliberately have no HTTP endpoint.

    python -m app.cli reset-password <username> [--password <new>]

The reset writes a new argon2id hash, sets `must_change_password` and revokes every refresh session of the user
(documented in README, «Восстановление доступа»). Run it inside the Hub container (`docker compose exec hub ...`).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from fastapi import HTTPException


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Hub recovery commands")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset-password", help="set a new password and require a change at the next sign-in")
    reset.add_argument("username", help="the local Hub account name")
    reset.add_argument("--password", help="the new password (asked for when omitted)")
    return parser


async def _reset(username: str, password: str | None) -> int:
    from app import db
    from app.bootstrap import reset_password

    if not password:
        password = getpass.getpass(f"Новый пароль для «{username}»: ")
        if password != getpass.getpass("Повторите пароль: "):
            print("Пароли не совпадают", file=sys.stderr)
            return 2
    db.init_engine()
    try:
        await db.connect_with_retry()
        user = await reset_password(username, password)
    except HTTPException as e:
        print(e.detail, file=sys.stderr)
        return 1
    finally:
        await db.dispose()
    print(f"Пароль пользователя «{user.username}» изменён. При следующем входе Hub попросит задать новый.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "reset-password":
        return asyncio.run(_reset(args.username, args.password))
    return 2  # pragma: no cover - argparse rejects unknown commands itself


if __name__ == "__main__":
    raise SystemExit(main())
