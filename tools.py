"""
Sample MCP tools that simulate production workloads.

Each tool has realistic latency, occasional errors, and varying result sizes.
Chaos mode can be toggled at runtime to degrade performance for demo purposes.
"""

import os
import random
import time
import math
import hashlib
from datetime import datetime, timezone

from fastmcp import FastMCP
from prometheus_client import Histogram
from mcp_metrics import metrics_registry, MCPMetricsWrapper

# ── Chaos mode (toggled via chaos_control tool or env var) ───────────────
_chaos = {
    "enabled": os.getenv("CHAOS_ENABLED", "false").lower() == "true",
    "extra_latency_ms": 500,
    "error_rate_boost": 0.25,
}

# ── Create and wrap the MCP server ──────────────────────────────────────
mcp_base = FastMCP("MCP-Observability-Demo", stateless_http=True)
wrapper = MCPMetricsWrapper(metrics_registry)
mcp = wrapper.wrap_mcp(mcp_base)

# ── Custom tool-specific metrics ────────────────────────────────────────
metrics_registry.register(
    "query_rows_returned",
    Histogram(
        "mcp_query_rows_returned",
        "Rows returned per query",
        buckets=[1, 5, 10, 25, 50, 100, 500],
    ),
)


def _maybe_chaos():
    """Apply chaos: extra latency and boosted error rate."""
    if not _chaos["enabled"]:
        return
    time.sleep(_chaos["extra_latency_ms"] / 1000)
    if random.random() < _chaos["error_rate_boost"]:
        raise RuntimeError("Chaos injection: simulated failure")


# ── Tool 1: Database Query ──────────────────────────────────────────────
@mcp.tool(name="query_database", description="Execute a read-only database query")
def query_database(query: str, limit: int = 100) -> dict:
    """Simulates a database query with variable latency."""
    _maybe_chaos()
    time.sleep(random.uniform(0.01, 0.05))

    complexity = len(query.split()) / 10
    time.sleep(random.uniform(0.05, 0.3) * max(1, complexity))

    if random.random() < 0.05:
        raise TimeoutError(f"Query timed out: {query[:50]}")

    row_count = min(limit, random.randint(1, 50))
    rows = [
        {"id": i, "value": f"row_{i}", "score": round(random.random() * 100, 2)}
        for i in range(row_count)
    ]

    m = metrics_registry.get("query_rows_returned")
    if m:
        m.observe(row_count)

    return {
        "rows": rows,
        "count": row_count,
        "query_time_ms": round(random.uniform(10, 300), 1),
    }


# ── Tool 2: Text Summarization ─────────────────────────────────────────
@mcp.tool(name="summarize_text", description="Summarize a block of text")
def summarize_text(text: str, max_length: int = 200) -> dict:
    """Simulates an LLM summarization call."""
    _maybe_chaos()
    time.sleep(random.uniform(0.1, 0.8))

    if random.random() < 0.03:
        raise ValueError("Input text exceeds maximum token limit")

    words = text.split()
    summary_len = min(max_length, max(10, len(words) // 3))
    summary = " ".join(words[:summary_len]) + "..."

    return {
        "summary": summary,
        "original_length": len(text),
        "summary_length": len(summary),
        "compression_ratio": round(len(summary) / max(1, len(text)), 3),
    }


# ── Tool 3: File Analysis ──────────────────────────────────────────────
@mcp.tool(name="analyze_file", description="Analyze file metadata and content")
def analyze_file(filename: str) -> dict:
    """Simulates file analysis with I/O latency."""
    _maybe_chaos()
    time.sleep(random.uniform(0.02, 0.15))

    if random.random() < 0.04:
        raise FileNotFoundError(f"File not found: {filename}")

    size = random.randint(100, 500_000)
    return {
        "filename": filename,
        "size_bytes": size,
        "lines": size // random.randint(40, 120),
        "checksum": hashlib.md5(filename.encode()).hexdigest(),
        "language": random.choice(["python", "javascript", "go", "rust", "java"]),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Tool 4: Health Check ───────────────────────────────────────────────
@mcp.tool(name="health_check", description="Check health of an external service")
def health_check(service_url: str, timeout_seconds: float = 5.0) -> dict:
    """Simulates an HTTP health check."""
    _maybe_chaos()
    latency = random.uniform(0.01, 0.5)
    time.sleep(latency)

    if random.random() < 0.08:
        raise ConnectionError(f"Connection refused: {service_url}")

    status = random.choices(
        ["healthy", "degraded", "unhealthy"], weights=[85, 10, 5]
    )[0]
    return {
        "service": service_url,
        "status": status,
        "latency_ms": round(latency * 1000, 1),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Tool 5: Calculate Metrics ──────────────────────────────────────────
@mcp.tool(name="calculate_metrics", description="Compute statistical metrics")
def calculate_metrics(values: list[float]) -> dict:
    """Computes stats on a list of numbers."""
    _maybe_chaos()
    if not values:
        raise ValueError("Empty values list")

    time.sleep(random.uniform(0.005, 0.05))

    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return {
        "count": n,
        "mean": round(mean, 4),
        "std_dev": round(math.sqrt(variance), 4),
        "min": min(values),
        "max": max(values),
        "p50": round(sorted(values)[n // 2], 4),
    }


# ── Tool 6: Read Resource (file read with resource metrics) ────────────
@mcp.tool(name="read_resource", description="Read a file resource by path")
def read_resource(path: str) -> dict:
    """Simulates reading a file resource. Populates resource access metrics."""
    _maybe_chaos()
    resource_id = f"file://{path}"
    import threading
    client_id = getattr(threading.current_thread(), "mcp_client_id", "unknown")
    metrics_registry.resource_accessed_total.labels(resource=resource_id, client_id=client_id).inc()

    time.sleep(random.uniform(0.01, 0.1))

    if random.random() < 0.06:
        metrics_registry.resource_errors_total.labels(
            resource=resource_id, error_type="PermissionError"
        ).inc()
        raise PermissionError(f"Access denied: {path}")

    # Simulate file content
    size = random.randint(200, 100_000)
    content = f"[simulated content of {path}, {size} bytes]"
    metrics_registry.resource_size_bytes.labels(resource=resource_id).observe(size)

    return {
        "resource": resource_id,
        "size_bytes": size,
        "content_preview": content[:200],
        "mime_type": "text/plain",
    }


# ── Tool 7: Chaos Control ──────────────────────────────────────────────
@mcp.tool(name="chaos_control", description="Toggle chaos mode for demo")
def chaos_control(
    enabled: bool = True,
    extra_latency_ms: int = 500,
    error_rate_boost: float = 0.25,
) -> dict:
    """Toggle chaos injection. When enabled, all tools get extra latency
    and a boosted error rate — useful for demoing 'before vs after' on dashboards."""
    _chaos["enabled"] = enabled
    _chaos["extra_latency_ms"] = extra_latency_ms
    _chaos["error_rate_boost"] = error_rate_boost
    return {
        "chaos_enabled": _chaos["enabled"],
        "extra_latency_ms": _chaos["extra_latency_ms"],
        "error_rate_boost": _chaos["error_rate_boost"],
        "message": "Chaos mode ENABLED — tools will be degraded"
        if enabled
        else "Chaos mode DISABLED — tools back to normal",
    }
