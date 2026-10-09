# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Run whole synchronous database operations on one bounded worker thread."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial


class DatabaseWorker:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studybot-db")
        self._closed = False

    async def run(self, function, *args, **kwargs):
        if self._closed:
            raise RuntimeError("Database worker is closed")
        future = asyncio.get_running_loop().run_in_executor(
            self._executor, partial(function, *args, **kwargs)
        )
        # Do not release caller-held locks while a cancelled write is still running.
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await asyncio.shield(future)
            raise

    def close(self):
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)
