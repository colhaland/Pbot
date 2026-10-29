import argparse
import asyncio
import getpass
import logging

from argon2 import PasswordHasher
from sqlalchemy import func, select, text

from app.db import SessionLocal, create_schema, engine
from app.models import Role, User
from app.settings import get_settings

logger = logging.getLogger("pbot.bootstrap")


async def create_owner_if_empty(email: str, password: str) -> bool:
    normalized_email = email.strip().lower()
    if len(normalized_email) < 3 or "@" not in normalized_email:
        raise ValueError("Email bootstrap owner tidak valid.")
    if len(password) < 12:
        raise ValueError("Password bootstrap minimal 12 karakter.")

    async with SessionLocal() as db:
        async with db.begin():
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": 731_902_114})
            users = await db.scalar(select(func.count()).select_from(User))
            if users:
                return False
            db.add(
                User(
                    email=normalized_email,
                    password_hash=PasswordHasher().hash(password),
                    role=Role.owner,
                    is_active=True,
                )
            )
    return True


async def ensure_bootstrap_owner() -> bool:
    settings = get_settings()
    email = settings.bootstrap_owner_email
    secret = settings.bootstrap_owner_password
    password = secret.get_secret_value() if secret else ""
    if not email and not password:
        return False
    if not email or not password:
        raise RuntimeError("BOOTSTRAP_OWNER_EMAIL dan BOOTSTRAP_OWNER_PASSWORD harus disetel bersama.")
    created = await create_owner_if_empty(email, password)
    if created:
        logger.info("bootstrap_owner.created")
    else:
        logger.info("bootstrap_owner.skipped_existing_users")
    return created

async def bootstrap_owner(email: str, password: str, *, confirm_production: bool = False) -> None:
    settings = get_settings()
    if settings.app_env == "production" and not confirm_production:
        raise SystemExit("Bootstrap production memerlukan flag --confirm-production dan akses terminal.")
    await create_schema()
    created = await create_owner_if_empty(email, password)
    if not created:
        raise SystemExit("Bootstrap ditolak: pengguna sudah tersedia.")
    print(f"Owner berhasil dibuat: {email.strip().lower()}")
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