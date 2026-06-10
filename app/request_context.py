"""Per-request context shared between the HTTP layer and logging.

Kept in its own module so both ``app.main`` (sets the value) and
``app.log_setup`` (reads it from log records) can import it without cycles.
"""
from contextvars import ContextVar

# Short correlation id for the current HTTP request (None outside a request).
# Set by the request-id middleware in app.main and never reset: contexts are
# per-task in asyncio, so the value naturally dies with the request task —
# and NOT resetting it is what lets SSE generator code (which runs after the
# middleware returned the StreamingResponse) still log with the right id.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
