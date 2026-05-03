"""Shared context for passing client metadata across async boundaries."""
import contextvars

# These context vars propagate across async tasks in the same context
current_client_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_client_id", default="unknown"
)
current_transport: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_transport", default="unknown"
)
