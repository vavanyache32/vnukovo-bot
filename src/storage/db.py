"""SQLAlchemy 2.0 async storage. SQLite/Postgres compatible.

Schema is intentionally compact: SQLite is fine for one-bot scale.
Migrations live in :mod:`storage.migrations` (alembic).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..config import Settings, get_settings
from ..models import MetarObservation, Notification, ResolutionReport


class Base(DeclarativeBase):
    pass


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    station: Mapped[str] = mapped_column(String(8), index=True)
    issue_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    raw: Mapped[str] = mapped_column(String(2048))
    raw_hash: Mapped[str] = mapped_column(String(32), index=True)
    temperature_c: Mapped[float] = mapped_column(Float)
    temperature_precision_c: Mapped[float] = mapped_column(Float, default=0.1)
    has_rmk_tgroup: Mapped[bool] = mapped_column(Boolean, default=False)
    is_speci: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint("station", "raw_hash", name="uq_obs_station_raw"),)


class StateRow(Base):
    __tablename__ = "state"
    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventRow(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotificationRow(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(4096))
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ResolutionRow(Base):
    __tablename__ = "resolutions"
    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"
    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_async_engine(url, future=True, echo=False)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        # Ensure SQLite directory exists
        if self.url.startswith("sqlite"):
            path = self.url.split("///", 1)[-1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("db: initialized at {}", self.url)

    def session(self) -> AsyncSession:
        return self.sessionmaker()


_DB: Database | None = None


def get_db(settings: Settings | None = None) -> Database:
    global _DB
    if _DB is None:
        s = settings or get_settings()
        _DB = Database(s.database_url)
    return _DB


async def init_db() -> None:
    await get_db().init()


# ----- helpers -----
async def save_observation(obs: MetarObservation) -> bool:
    """Insert observation, return True if it was new."""
    db = get_db()
    async with db.session() as s:
        existing = await s.execute(
            select(Observation).where(
                Observation.station == obs.station, Observation.raw_hash == obs.raw_hash
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False
        s.add(
            Observation(
                station=obs.station,
                issue_time=obs.issue_time,
                raw=obs.raw,
                raw_hash=obs.raw_hash,
                temperature_c=obs.temperature_c,
                temperature_precision_c=obs.temperature_precision_c,
                has_rmk_tgroup=obs.has_rmk_tgroup,
                is_speci=obs.is_speci,
                source=obs.source.value,
            )
        )
        await s.commit()
        return True


async def load_state(slug: str) -> dict[str, Any]:
    db = get_db()
    async with db.session() as s:
        row = await s.get(StateRow, slug)
        return dict(row.payload) if row else {}


async def save_state(slug: str, payload: dict[str, Any]) -> None:
    db = get_db()
    async with db.session() as s:
        row = await s.get(StateRow, slug)
        if row is None:
            s.add(StateRow(slug=slug, payload=payload, updated_at=datetime.utcnow()))
        else:
            row.payload = payload
            row.updated_at = datetime.utcnow()
        await s.commit()


async def save_event(event_id: str, slug: str, payload: dict[str, Any]) -> None:
    db = get_db()
    async with db.session() as s:
        row = await s.get(EventRow, event_id)
        if row is None:
            s.add(
                EventRow(
                    event_id=event_id, slug=slug, payload=payload, fetched_at=datetime.utcnow()
                )
            )
        else:
            row.payload = payload
            row.fetched_at = datetime.utcnow()
        await s.commit()


async def save_notification(n: Notification) -> None:
    db = get_db()
    async with db.session() as s:
        s.add(
            NotificationRow(
                severity=n.severity.value,
                title=n.title,
                body=n.body,
                dedup_key=n.dedup_key,
                created_at=n.created_at,
                payload=n.payload,
            )
        )
        await s.commit()


async def save_resolution(report: ResolutionReport) -> None:
    db = get_db()
    async with db.session() as s:
        row = await s.get(ResolutionRow, report.slug)
        payload = json.loads(report.model_dump_json())
        if row is None:
            s.add(
                ResolutionRow(
                    slug=report.slug,
                    payload=payload,
                    finalized=report.finalized,
                    generated_at=report.generated_at,
                )
            )
        elif row.finalized:
            # Lock after publication: do not rewrite
            logger.warning("resolver: refusing to rewrite finalized resolution for {}", report.slug)
            return
        else:
            row.payload = payload
            row.finalized = report.finalized
            row.generated_at = report.generated_at
        await s.commit()


# ----- subscription helpers -----
async def save_subscription(slug: str) -> None:
    db = get_db()
    async with db.session() as s:
        row = await s.get(SubscriptionRow, slug)
        if row is None:
            s.add(SubscriptionRow(slug=slug, active=True, created_at=datetime.utcnow()))
        else:
            row.active = True
        await s.commit()


async def remove_subscription(slug: str) -> None:
    db = get_db()
    async with db.session() as s:
        row = await s.get(SubscriptionRow, slug)
        if row is not None:
            row.active = False
            await s.commit()


async def load_subscriptions() -> list[str]:
    db = get_db()
    async with db.session() as s:
        result = await s.execute(select(SubscriptionRow.slug).where(SubscriptionRow.active == True))
        return [r[0] for r in result.all()]
