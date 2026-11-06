"""Event-loop bootstrap for ib_insync / eventkit on Python 3.13+.

``eventkit`` (a dependency of ib_insync) calls
``asyncio.get_event_loop_policy().get_event_loop()`` at *import time*.
On Python 3.14 that raises ``RuntimeError: There is no current event loop``
when the importing thread has no loop set — which is exactly what happens
inside Streamlit's ScriptRunner thread.

Import this module BEFORE ib_insync, or call :func:`ensure_event_loop` first.
The IB client itself is owned by a dedicated loop thread (see
``marketmind.broker.ib_adapter``); this module only guarantees that importing
the library is safe from any thread.
"""

from __future__ import annotations

import asyncio


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return a usable event loop for the current thread, creating one if needed."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("current event loop is closed")
        return loop
    except (RuntimeError, DeprecationWarning):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# Applied at import time so ``from ib_insync import ...`` never explodes.
ensure_event_loop()

__all__ = ["ensure_event_loop"]
