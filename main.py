"""
MCP Server with Full Production Observability.

Stack:
  - Prometheus metrics (tool execution, protocol messages, sessions, resources, system)
  - Distributed tracing via OpenTelemetry → Grafana Tempo
  - Protocol middleware intercepting MCP JSON-RPC messages
  - System metrics (CPU/memory) collected every 15s
  - All MCP tools auto-instrumented (metrics + traces)

Ports:
  8000 — FastAPI (REST + /metrics)
  8001 — MCP server (streamable-http) with observability middleware
"""

import os
import threading
import time

import psutil
import uvicorn
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ── OpenTelemetry setup (before any tracer usage) ───────────────────────
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

TEMPO_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

resource = Resource.create({"service.name": "mcp-server"})
provider = TracerProvider(resource=resource)

try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    exporter = OTLPSpanExporter(endpoint=TEMPO_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
except Exception as e:
    print(f"Warning: OTLP exporter not available: {e}")

trace.set_tracer_provider(provider)

# ── Import app modules ──────────────────────────────────────────────────
from mcp_metrics import metrics_registry, MCPProtocolMiddleware
from mcp_metrics.context import current_client_id, current_transport

mcp = None
mcp_base = None
try:
    from tools import mcp as _mcp, mcp_base as _mcp_base
    mcp = _mcp
    mcp_base = _mcp_base
except Exception as e:
    print(f"Warning: failed to import MCP tools: {e}")

# ── FastAPI app (port 8000: REST + /metrics) ────────────────────────────
app = FastAPI(
    title="MCP Observability Demo",
    version="1.0.0",
    description="Production MCP server monitoring: Prometheus + Grafana + Tempo",
)

# Prometheus auto-instrumentation for HTTP layer
Instrumentator().instrument(app).expose(app)


@app.get("/")
async def root():
    return {
        "service": "MCP Observability Demo",
        "mcp_endpoint": "http://localhost:8001/mcp",
        "metrics": "http://localhost:8000/metrics",
        "grafana": "http://localhost:3000",
        "tempo": "http://localhost:3000/explore (select Tempo datasource)",
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "mcp_available": mcp is not None}


# ── Background system metrics collector ─────────────────────────────────
def _collect_system_metrics():
    while True:
        try:
            metrics_registry.cpu_usage_percent.set(psutil.cpu_percent(interval=1))
            mem = psutil.virtual_memory()
            metrics_registry.memory_usage_mb.set(mem.used / (1024 * 1024))
        except Exception:
            pass
        time.sleep(15)


# ── MCP Observability Middleware (for port 8001) ────────────────────────
class MCPClientIdMiddleware(BaseHTTPMiddleware):
    """Extracts client_id from MCP requests and sets it in context vars."""

    async def dispatch(self, request: Request, call_next):
        client_id = self._extract_client_id(request)
        transport = self._detect_transport(request)

        # Set context vars so the wrapper can pick them up
        current_client_id.set(client_id)
        current_transport.set(transport)

        # Also set on thread for sync tool functions
        threading.current_thread().mcp_client_id = client_id

        # Track session metrics
        session_id = (
            request.headers.get("mcp-session-id")
            or request.headers.get("x-session-id")
            or ""
        )
        if session_id:
            metrics_registry.sessions_total.labels(
                client_id=client_id, transport=transport
            ).inc()
            metrics_registry.connection_total.labels(
                transport=transport, client_id=client_id
            ).inc()
            metrics_registry.transport_type_active.labels(transport=transport).inc()

        response = await call_next(request)
        return response

    def _extract_client_id(self, request: Request) -> str:
        client_id = request.headers.get("x-client-id")
        if client_id:
            return client_id

        ua = request.headers.get("user-agent", "")
        if "kiro" in ua.lower():
            return "kiro"
        elif "claude" in ua.lower() or "anthropic" in ua.lower():
            return "claude-desktop"
        elif "cursor" in ua.lower():
            return "cursor"
        elif "vscode" in ua.lower() or "visual-studio" in ua.lower():
            return "vscode"
        elif "python" in ua.lower() or "httpx" in ua.lower():
            return "python-client"
        elif "fastmcp" in ua.lower():
            return "fastmcp-client"
        else:
            return ua[:30] if ua else "unknown"

    def _detect_transport(self, request: Request) -> str:
        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            return "sse"
        elif request.headers.get("upgrade", "").lower() == "websocket":
            return "websocket"
        else:
            return "streamable-http"


# ── MCP server runner (port 8001 with middleware) ───────────────────────
def _start_mcp_server():
    if mcp is None:
        print("MCP not available; skipping")
        return
    print("Starting MCP server on port 8001 with observability middleware...")
    try:
        # Use http_app() to get ASGI app, then add middleware
        middleware = [Middleware(MCPClientIdMiddleware)]
        mcp_app = mcp.http_app(path="/mcp", middleware=middleware)
        uvicorn.run(mcp_app, host="0.0.0.0", port=8001)
    except Exception as e:
        print(f"MCP server error: {e}")


# ── Entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=_collect_system_metrics, daemon=True).start()
    threading.Thread(target=_start_mcp_server, daemon=False).start()
    uvicorn.run(app, host="0.0.0.0", port=8000)
