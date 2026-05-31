"""charlie/integrations/adapter.py

Sync/async call adapter for integration methods.

Ensures a sync method is never awaited directly and an async method is never
called in a blocking fashion.  All integration calls from async contexts
(e.g. ControlServer handlers) should go through ``call_integration``.

Requirements: 11.4
"""

import asyncio
import inspect
from typing import Any, Callable


async def call_integration(method: Callable, *args: Any, **kwargs: Any) -> Any:
    """Invoke *method* correctly regardless of whether it is sync or async.

    - If *method* is a coroutine function, it is awaited directly.
    - Otherwise it is offloaded to a thread via ``asyncio.to_thread`` so the
      event loop is never blocked by a synchronous integration call.
    """
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await asyncio.to_thread(method, *args, **kwargs)
