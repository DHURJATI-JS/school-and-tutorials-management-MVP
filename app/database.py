"""Async SQLAlchemy engine/session + database bootstrap (pgvector extension, tables)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.util.concurrency import await_
from sqlalchemy.util.langhelpers import memoized_property
from sqlalchemy.util.queue import AsyncAdaptedQueue, Empty

from .config import settings

log = logging.getLogger("classnest.db")


class FairAsyncQueue(AsyncAdaptedQueue):
    """First-come-first-served connection checkout.

    SQLAlchemy's asyncio queue lets a newly arriving request grab a just-returned connection before the
    request that was already woken up for it ("barging"); under a burst the unlucky waiter goes back to
    the end of the line again and again. Load testing showed 14-26 s tail latency (close to the 30 s
    pool timeout -> HTTP 500) while the median stayed ~50 ms. Here waiters queue behind a FIFO lock and a
    non-blocking checkout never jumps the line (it opens an overflow connection instead)."""

    @memoized_property
    def _turnstile(self) -> asyncio.Lock:  # created lazily inside the running event loop
        return asyncio.Lock()

    def _line_is_busy(self) -> bool:
        lock = self._turnstile
        return lock.locked() or bool(getattr(lock, "_waiters", None))

    async def _fair_get(self):
        async with self._turnstile:
            return await self._queue.get()

    def get(self, block: bool = True, timeout: float | None = None):
        if not self._line_is_busy():
            try:  # nobody is waiting: take an idle connection straight away (no lock, no context switch)
                return self.get_nowait()
            except Empty:
                if not block:
                    raise
        elif not block:
            raise Empty()  # others are queued: don't jump the line (the pool opens an overflow connection)
        try:
            if timeout is not None:
                return await_(asyncio.wait_for(self._fair_get(), timeout))
            return await_(self._fair_get())
        except (asyncio.QueueEmpty, asyncio.TimeoutError) as err:
            raise Empty() from err


class FairAsyncAdaptedQueuePool(AsyncAdaptedQueuePool):
    _queue_class = FairAsyncQueue


engine = create_async_engine(
    settings.database_url,
    echo=settings.DB_ECHO,
    poolclass=FairAsyncAdaptedQueuePool,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one AsyncSession per request."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db(retries: int = 15, delay: float = 2.0) -> None:
    """Create the pgvector extension and all tables. Retries briefly while Postgres boots."""
    from . import models  # noqa: F401  (register mappers)

    where = (settings.DATABASE_URL.split("@")[-1] if settings.DATABASE_URL
             else f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(models.Base.metadata.create_all)
            log.info("Database ready (%s)", where)
            return
        except DBAPIError as exc:
            message = str(exc).lower()
            if "vector" in message and ("extension" in message or "control file" in message):
                raise RuntimeError(
                    f"PostgreSQL at {where} is running but the pgvector extension is not installed. Use the "
                    "pgvector/pgvector Docker image (docker compose up -d db) or install pgvector.") from exc
            if isinstance(exc, ProgrammingError):
                raise
            last_error = exc
        except OSError as exc:
            last_error = exc
        if attempt == 1:
            log.error("PostgreSQL is not reachable at %s (%s). Start it with:  docker compose up -d db  "
                      "(needs Docker Desktop) - or point DATABASE_URL at another PostgreSQL that has pgvector. "
                      "Retrying for ~%ss...", where, last_error.__class__.__name__ if last_error else "?",
                      int(retries * (delay + 2)))
        else:
            log.warning("Waiting for PostgreSQL (attempt %s/%s)...", attempt, retries)
        await asyncio.sleep(delay)
    raise RuntimeError(
        f"Could not connect to PostgreSQL at {where}. Start it with `docker compose up -d db` "
        "(Docker Desktop) or set DATABASE_URL - see README › Quick start."
    ) from last_error
