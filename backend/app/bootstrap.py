import argparse
import asyncio
import getpass

from argon2 import PasswordHasher
from sqlalchemy import func, select

from app.db import SessionLocal, create_schema, engine
from app.models import Role, User
from app.settings import get_settings


async def bootstrap_owner(email: str, password: str, *, confirm_production: bool = False) -> None:
    settings = get_settings()
    if settings.app_env == "production" and not confirm_production:
        raise SystemExit("Bootstrap production memerlukan flag --confirm-production dan akses terminal.")
    await create_schema()
    async with SessionLocal() as db:
        users = await db.scalar(select(func.count()).select_from(User))
        if users:
            raise SystemExit("Bootstrap ditolak: pengguna sudah tersedia.")
        user = User(email=email.strip().lower(), password_hash=PasswordHasher().hash(password), role=Role.owner, is_active=True)
        db.add(user)
        await db.commit()
        print(f"Owner berhasil dibuat: {user.email}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Membuat owner pertama Pbot pada instalasi development.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args()
    password = getpass.getpass("Password owner: ")
    confirmation = getpass.getpass("Ulangi password: ")
    if len(password) < 12 or password != confirmation:
        raise SystemExit("Password harus minimal 12 karakter dan kedua input harus sama.")
    asyncio.run(bootstrap_owner(args.email, password, confirm_production=args.confirm_production))


if __name__ == "__main__":
    main()