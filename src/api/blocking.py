"""Bound synchronous MCP work without occupying the HTTP event loop."""
from functools import partial
from typing import Callable, TypeVar

import anyio

from src.core.config import settings

T = TypeVar("T")


class MCPBusyError(RuntimeError):
    def __init__(self):
        super().__init__("Knowledge is busy; retry the request shortly.")


class BlockingWorkPool:
    def __init__(self, workers: int, max_waiting: int, timeout: float):
        self.admission = anyio.Semaphore(workers + max_waiting)
        self.workers = anyio.CapacityLimiter(workers)
        self.threads = anyio.CapacityLimiter(workers)
        self.timeout = timeout

    async def run(self, function: Callable[..., T], *args, **kwargs) -> T:
        try:
            self.admission.acquire_nowait()
        except anyio.WouldBlock:
            raise MCPBusyError() from None
        try:
            # Only queueing is timed out. A running DB/HTTP call keeps its slot
            # until it finishes, including when the request is cancelled.
            try:
                with anyio.fail_after(self.timeout):
                    await self.workers.acquire()
            except TimeoutError:
                raise MCPBusyError() from None
            try:
                # A separate token avoids reacquiring the caller's worker token.
                # AnyIO propagates the verified user's ContextVars to the thread.
                return await anyio.to_thread.run_sync(
                    partial(function, *args, **kwargs),
                    limiter=self.threads,
                )
            finally:
                self.workers.release()
        finally:
            self.admission.release()


mcp_work_pool = BlockingWorkPool(
    settings.mcp_thread_pool_size,
    settings.mcp_thread_pool_max_waiting,
    settings.mcp_thread_pool_timeout_seconds,
)
