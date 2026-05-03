"""
Auto-instrumentation wrapper for FastMCP tools.

Each tool invocation automatically:
  - Creates an OpenTelemetry span (exported to Jaeger/Tempo)
  - Records Prometheus metrics (call count, duration, errors, result size)
  - Tracks concurrent in-flight calls
  - Captures token usage estimates
  - Detects parameter hallucination patterns
"""

import asyncio
import functools
import json
import time
import threading

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from .registry import MCPMetricsRegistry, metrics_registry as _default
from .context import current_client_id

tracer = trace.get_tracer("mcp-tools")

# Thread-safe session tracking
_active_session_ids: set[str] = set()
_session_lock = threading.Lock()

# Rate limiting state: client_id -> (call_count, window_start)
_rate_state: dict[str, tuple[int, float]] = {}
_rate_lock = threading.Lock()
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 100  # max calls per window per client


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English)."""
    return max(1, len(text) // 4)


def _check_rate_limit(client_id: str, tool_name: str, reg: MCPMetricsRegistry) -> bool:
    """Returns True if rate-limited."""
    now = time.time()
    with _rate_lock:
        count, window_start = _rate_state.get(client_id, (0, now))
        if now - window_start > RATE_LIMIT_WINDOW:
            _rate_state[client_id] = (1, now)
            return False
        if count >= RATE_LIMIT_MAX:
            reg.rate_limit_total.labels(client_id=client_id, tool_name=tool_name).inc()
            return True
        _rate_state[client_id] = (count + 1, window_start)
        return False


class MCPMetricsWrapper:
    def __init__(self, registry: MCPMetricsRegistry | None = None) -> None:
        self.reg = registry or _default
        self._known_tools: set[str] = set()

    def wrap_mcp(self, mcp_instance):
        """Replace mcp.tool() with an instrumented version."""
        original_tool = mcp_instance.tool

        def instrumented_tool(*args, **kwargs):
            original_decorator = original_tool(*args, **kwargs)

            def wrapper(func):
                tool_name = kwargs.get("name", func.__name__)
                self._known_tools.add(tool_name)

                @functools.wraps(func)
                def sync_fn(*a, **kw):
                    return self._run_sync(func, tool_name, a, kw)

                @functools.wraps(func)
                async def async_fn(*a, **kw):
                    return await self._run_async(func, tool_name, a, kw)

                if asyncio.iscoroutinefunction(func):
                    return original_decorator(async_fn)
                return original_decorator(sync_fn)

            return wrapper

        mcp_instance.tool = instrumented_tool
        return mcp_instance

    @property
    def known_tools(self) -> set[str]:
        return self._known_tools

    def record_hallucination(self, attempted_tool: str, client_id: str = "unknown"):
        """Record when a client tries to call a non-existent tool."""
        if attempted_tool not in self._known_tools:
            self.reg.tool_hallucination_total.labels(
                attempted_tool=attempted_tool, client_id=client_id
            ).inc()

    def _extract_client_id(self, args, kwargs) -> str:
        """Try to extract client_id from context var, thread-local, or default."""
        # First try context var (works across async)
        cid = current_client_id.get("unknown")
        if cid != "unknown":
            return cid
        # Fallback to thread-local
        return getattr(threading.current_thread(), "mcp_client_id", "unknown")

    def _run_sync(self, func, tool_name, args, kwargs):
        client_id = self._extract_client_id(args, kwargs)

        # Rate limiting check
        if _check_rate_limit(client_id, tool_name, self.reg):
            raise RuntimeError(f"Rate limited: {client_id}")

        # Track in-flight
        self.reg.tool_inflight.labels(tool_name=tool_name).inc()
        self.reg.tool_inflight_total.inc()

        # Estimate input tokens
        input_str = json.dumps({"args": str(args), "kwargs": str(kwargs)})
        input_tokens = _estimate_tokens(input_str)
        self.reg.token_usage_total.labels(tool_name=tool_name, direction="input").inc(input_tokens)

        with tracer.start_as_current_span(
            f"mcp.tool.{tool_name}",
            attributes={
                "mcp.tool.name": tool_name,
                "mcp.client.id": client_id,
            },
        ) as span:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                self._ok(tool_name, start, result, span, client_id)
                return result
            except Exception as exc:
                self._fail(tool_name, start, exc, span, client_id)
                raise
            finally:
                self.reg.tool_inflight.labels(tool_name=tool_name).dec()
                self.reg.tool_inflight_total.dec()

    async def _run_async(self, func, tool_name, args, kwargs):
        client_id = self._extract_client_id(args, kwargs)

        if _check_rate_limit(client_id, tool_name, self.reg):
            raise RuntimeError(f"Rate limited: {client_id}")

        self.reg.tool_inflight.labels(tool_name=tool_name).inc()
        self.reg.tool_inflight_total.inc()

        input_str = json.dumps({"args": str(args), "kwargs": str(kwargs)})
        input_tokens = _estimate_tokens(input_str)
        self.reg.token_usage_total.labels(tool_name=tool_name, direction="input").inc(input_tokens)

        with tracer.start_as_current_span(
            f"mcp.tool.{tool_name}",
            attributes={
                "mcp.tool.name": tool_name,
                "mcp.client.id": client_id,
            },
        ) as span:
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                self._ok(tool_name, start, result, span, client_id)
                return result
            except Exception as exc:
                self._fail(tool_name, start, exc, span, client_id)
                raise
            finally:
                self.reg.tool_inflight.labels(tool_name=tool_name).dec()
                self.reg.tool_inflight_total.dec()

    def _ok(self, tool_name: str, start: float, result, span, client_id: str):
        dur = time.perf_counter() - start

        # Prometheus — tool metrics
        self.reg.tool_calls_total.labels(
            tool_name=tool_name, status="success", client_id=client_id
        ).inc()
        self.reg.tool_duration_seconds.labels(tool_name=tool_name).observe(dur)
        self.reg.tool_duration_quantiles.labels(tool_name=tool_name).observe(dur)

        try:
            payload = json.dumps(result).encode()
        except (TypeError, ValueError):
            payload = str(result).encode()
        self.reg.tool_result_size_bytes.labels(tool_name=tool_name).observe(len(payload))

        # Token usage (output)
        output_tokens = _estimate_tokens(payload.decode(errors="replace"))
        self.reg.token_usage_total.labels(tool_name=tool_name, direction="output").inc(output_tokens)
        self.reg.token_usage_per_call.labels(tool_name=tool_name).observe(output_tokens)

        # Protocol messages
        self.reg.messages_received_total.labels(msg_type="tools/call", client_id=client_id).inc()
        self.reg.messages_sent_total.labels(msg_type="tools/result").inc()
        self.reg.message_size_bytes.labels(direction="sent").observe(len(payload))

        # OpenTelemetry span
        span.set_attribute("mcp.tool.duration_ms", round(dur * 1000, 2))
        span.set_attribute("mcp.tool.result_size_bytes", len(payload))
        span.set_attribute("mcp.tool.status", "success")
        span.set_attribute("mcp.tool.output_tokens", output_tokens)
        span.set_status(StatusCode.OK)

    def _fail(self, tool_name: str, start: float, exc: Exception, span, client_id: str):
        dur = time.perf_counter() - start
        error_type = type(exc).__name__

        # Prometheus — tool metrics
        self.reg.tool_calls_total.labels(
            tool_name=tool_name, status="error", client_id=client_id
        ).inc()
        self.reg.tool_errors_total.labels(
            tool_name=tool_name, error_type=error_type
        ).inc()
        self.reg.tool_duration_seconds.labels(tool_name=tool_name).observe(dur)

        # Protocol messages
        self.reg.messages_received_total.labels(msg_type="tools/call", client_id=client_id).inc()
        self.reg.messages_sent_total.labels(msg_type="tools/error").inc()

        # OpenTelemetry span
        span.set_attribute("mcp.tool.duration_ms", round(dur * 1000, 2))
        span.set_attribute("mcp.tool.status", "error")
        span.set_attribute("mcp.tool.error_type", error_type)
        span.set_attribute("mcp.tool.error_message", str(exc)[:500])
        span.set_status(StatusCode.ERROR, str(exc)[:200])
        span.record_exception(exc)
