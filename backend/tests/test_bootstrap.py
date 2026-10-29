import pytest
from argon2 import PasswordHasher
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import bootstrap
from app.models import Base, Role, User
from app.settings import Settings


@pytest.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(bootstrap, "SessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_creates_one_owner_and_hashes_password(database):
    created = await bootstrap.create_owner_if_empty(" Owner@Example.Test ", "secure-bootstrap-password")
    assert created is True

    async with database() as db:
        user = await db.scalar(select(User))
        assert user.email == "owner@example.test"
        assert user.role == Role.owner
        assert user.password_hash != "secure-bootstrap-password"
        assert PasswordHasher().verify(user.password_hash, "secure-bootstrap-password")

    assert await bootstrap.create_owner_if_empty("another@example.test", "another-secure-password") is False
    async with database() as db:
        assert await db.scalar(select(User.email)) == "owner@example.test"


@pytest.mark.asyncio
async def test_bootstrap_rejects_weak_password_without_creating_user(database):
    with pytest.raises(ValueError, match="minimal 12 karakter"):
        await bootstrap.create_owner_if_empty("owner@example.test", "short")
    async with database() as db:
        assert await db.scalar(select(User)) is None


@pytest.mark.asyncio
async def test_empty_bootstrap_environment_is_ignored(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "get_settings",
        lambda: Settings(bootstrap_owner_email="", bootstrap_owner_password=SecretStr("")),
    )
    assert await bootstrap.ensure_bootstrap_owner() is False