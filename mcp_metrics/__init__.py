"""MCP Metrics — Prometheus + OpenTelemetry instrumentation for FastMCP tools."""

from .registry import MCPMetricsRegistry, metrics_registry
from .wrapper import MCPMetricsWrapper
from .middleware import MCPProtocolMiddleware, close_session
from .context import current_client_id, current_transport

__all__ = [
    "MCPMetricsRegistry",
    "metrics_registry",
    "MCPMetricsWrapper",
    "MCPProtocolMiddleware",
    "close_session",
    "current_client_id",
    "current_transport",
]
