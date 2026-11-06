"""Fire-and-forget background tasks with error logging and graceful shutdown."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("classnest.bg")
_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str = "task") -> asyncio.Task:
    async def runner() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Background task '%s' failed", name)

    task = asyncio.get_running_loop().create_task(runner(), name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain(timeout: float = 10.0) -> None:
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=timeout)
