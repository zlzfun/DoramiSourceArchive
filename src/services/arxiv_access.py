"""One polite arXiv request at a time per application event loop."""
import asyncio
import time
import weakref
from contextlib import asynccontextmanager

_states = weakref.WeakKeyDictionary()


@asynccontextmanager
async def arxiv_request():
    loop = asyncio.get_running_loop()
    lock, previous = _states.setdefault(loop, (asyncio.Lock(), [0.0]))
    async with lock:
        await asyncio.sleep(max(0.0, previous[0] + 3.1 - time.monotonic()))
        previous[0] = time.monotonic()
        yield
