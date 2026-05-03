"""
FastAPI middleware that intercepts MCP JSON-RPC traffic on /mcp.

Captures:
  - Protocol message counts (received/sent) by type and client
  - Message payload sizes and latency
  - Protocol version from initialize requests
  - Session lifecycle (connect/disconnect, duration, active count)
  - Handshake success/failure
  - JSON-RPC error codes
  - Transport type tracking
  - Client identification for multi-client dashboards
"""

import json
import time
import uuid
import threading
import contextvars

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .registry import metrics_registry as reg
from .context import current_client_id, current_transport

# Track sessions: session_id -> {start_time, client_id, transport}
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _extract_client_id(request: Request) -> str:
    """Extract client identifier from request headers.

    Clients can identify themselves via:
      - X-Client-Id header (custom)
      - User-Agent parsing (fallback)
    """
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
        return "webapp"
    else:
        return ua[:30] if ua else "unknown"


def _detect_transport(request: Request) -> str:
    """Detect transport type from request characteristics."""
    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return "sse"
    elif request.headers.get("upgrade", "").lower() == "websocket":
        return "websocket"
    else:
        return "streamable-http"


class MCPProtocolMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only instrument the MCP endpoint
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)

        client_id = _extract_client_id(request)
        transport = _detect_transport(request)
        session_id = request.headers.get("x-session-id") or request.headers.get("mcp-session-id") or str(uuid.uuid4())
        request_start = time.time()

        # Set client_id on thread and context var for wrapper to pick up
        threading.current_thread().mcp_client_id = client_id
        current_client_id.set(client_id)
        current_transport.set(transport)

        # ── Track session lifecycle ──────────────────────────────────
        with _sessions_lock:
            if session_id not in _sessions:
                _sessions[session_id] = {
                    "start_time": time.time(),
                    "client_id": client_id,
                    "transport": transport,
                }
                reg.sessions_total.labels(client_id=client_id, transport=transport).inc()
                reg.active_sessions.inc()
                reg.active_sessions_by_client.labels(client_id=client_id).inc()
                reg.connection_total.labels(transport=transport, client_id=client_id).inc()
                reg.transport_type_active.labels(transport=transport).inc()

        # ── Read and measure incoming message ────────────────────────
        body = b""
        msg_type = "unknown"
        try:
            body = await request.body()
            if body:
                reg.message_size_bytes.labels(direction="received").observe(len(body))
                payload = json.loads(body)
                msg_type = payload.get("method", "response")
                reg.messages_received_total.labels(msg_type=msg_type, client_id=client_id).inc()

                # Capture protocol version from initialize
                if msg_type == "initialize":
                    version = payload.get("params", {}).get("protocolVersion", "unknown")
                    reg.protocol_version_count.labels(version=version, client_id=client_id).inc()
                    reg.handshake_total.labels(status="success", transport=transport).inc()

        except (json.JSONDecodeError, UnicodeDecodeError):
            reg.jsonrpc_errors_total.labels(
                error_code="-32700", error_message="Parse error"
            ).inc()

        # ── Forward request ──────────────────────────────────────────
        try:
            response = await call_next(request)
        except Exception as exc:
            # Handshake failure if this was an initialize
            if msg_type == "initialize":
                reg.handshake_total.labels(status="failure", transport=transport).inc()
            raise

        # ── Record request-response latency ──────────────────────────
        latency = time.time() - request_start
        reg.message_latency_seconds.labels(msg_type=msg_type).observe(latency)

        # ── Measure outgoing message ─────────────────────────────────
        if isinstance(response, StreamingResponse):
            reg.messages_sent_total.labels(msg_type="stream").inc()
        else:
            resp_body = b""
            async for chunk in response.body_iterator:
                resp_body += chunk if isinstance(chunk, bytes) else chunk.encode()
            if resp_body:
                reg.message_size_bytes.labels(direction="sent").observe(len(resp_body))
                try:
                    resp_payload = json.loads(resp_body)
                    resp_type = resp_payload.get("method", "response")
                    reg.messages_sent_total.labels(msg_type=resp_type).inc()

                    # Track JSON-RPC errors in responses
                    if "error" in resp_payload:
                        err = resp_payload["error"]
                        code = str(err.get("code", "unknown"))
                        msg = err.get("message", "unknown")[:50]
                        reg.jsonrpc_errors_total.labels(
                            error_code=code, error_message=msg
                        ).inc()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    reg.messages_sent_total.labels(msg_type="response").inc()

            response = Response(
                content=resp_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response


def close_session(session_id: str, reason: str = "normal"):
    """Call when a session disconnects to record duration."""
    with _sessions_lock:
        session_info = _sessions.pop(session_id, None)
    if session_info:
        client_id = session_info["client_id"]
        transport = session_info["transport"]
        reg.active_sessions.dec()
        reg.active_sessions_by_client.labels(client_id=client_id).dec()
        reg.transport_type_active.labels(transport=transport).dec()
        reg.session_disconnects_total.labels(client_id=client_id, reason=reason).inc()
        reg.session_duration_seconds.observe(time.time() - session_info["start_time"])
