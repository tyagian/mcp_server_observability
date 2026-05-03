# MCP Server Observability

A production-grade observability stack for [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers. Any MCP-compatible client can connect — AI assistants (Claude Desktop, Kiro, Cursor), custom applications, or programmatic scripts using the FastMCP client library.


This project provides a ready-to-run monitoring setup with:
- **Prometheus** — 40+ custom metrics across 7 observability layers (tool execution, protocol messages, sessions, resources, agentic/LLM behavior, rate limiting, system health)
- **Grafana dashboards** — pre-built panels for tool call rates, latency percentiles, error classification, client identification, and concurrency tracking
- **Distributed tracing (Tempo)** — OpenTelemetry spans for every tool invocation with full context (tool name, client ID, duration, token usage, error details)
- **Alerting rules** — error rate, latency, CPU thresholds with configurable severity

Includes a MCP server with 7 sample tools, chaos injection for before/after demos, and load testing scripts to generate realistic traffic patterns.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  MCP Client  │────▶│   MCP Server     │────▶│ Prometheus  │
│  (curl/Kiro) │     │   :8001/mcp      │     │   :9090     │
└─────────────┘     └────────┬─────────┘     └──────┬──────┘
                             │                       │
                      ┌──────┴─────────┐     ┌──────┴──────┐
                      │   FastAPI      │     │   Grafana   │
                      │   :8000        │     │   :3000     │
                      │   /metrics     │     └──────┬──────┘
                      └──────┬─────────┘            │
                             │               ┌──────┴──────┐
                      ┌──────┴─────────┐     │   Tempo     │
                      │  OpenTelemetry │────▶│   :4317     │
                      │  (traces)      │     │  (traces)   │
                      └────────────────┘     └─────────────┘
```

## Observability Layers

### 1. Tool Execution Metrics
- `mcp_tool_calls_total` — invocation count by tool and status
- `mcp_tool_duration_seconds` — execution time histogram (p50/p95)
- `mcp_tool_errors_total` — errors classified by type
- `mcp_tool_result_size_bytes` — response payload sizes

### 2. Protocol Message Metrics
- `mcp_messages_received_total` / `mcp_messages_sent_total` — by message type
- `mcp_message_size_bytes` — payload size distribution
- `mcp_protocol_version_count` — client version tracking

### 3. Session Metrics
- `mcp_active_sessions` — current session count
- `mcp_session_duration_seconds` — session lifetime histogram

### 4. Resource Access Metrics
- `mcp_resource_accessed_total` — access frequency by resource
- `mcp_resource_size_bytes` — response sizes
- `mcp_resource_errors_total` — access errors by type

### 5. System Resources
- `mcp_cpu_usage_percent`, `mcp_memory_usage_mb`

### 6. Distributed Tracing
- OpenTelemetry spans for every tool call → Grafana Tempo
- Span attributes: tool name, duration, status, error type, result size

### 7. Alerting
- Error rate > 10% for 2 min → critical
- p95 latency > 2s for 3 min → warning
- CPU > 90% for 5 min → warning
- Resource access errors → warning

## Quick Start

```bash
docker compose up -d --build
```

| Service | URL | Credentials |
|---------|-----|-------------|
| FastAPI | http://localhost:8000 | — |
| MCP endpoint | http://localhost:8001/mcp | — |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin / admin123 |
| Jaeger UI | http://localhost:16686 | — |

## Connecting Clients

The MCP server listens on `http://localhost:8001/mcp` using the standard **streamable-http** transport. Any MCP client can connect.

### Python (FastMCP client)
```python
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

transport = StreamableHttpTransport(
    "http://localhost:8001/mcp",
    headers={"X-Client-Id": "my-app"},  # optional: identifies your client in metrics
)

async with Client(transport=transport) as client:
    result = await client.call_tool("calculate_metrics", {"values": [10, 20, 30, 40, 50]})
    print(result)
```

### Claude Desktop / Kiro / Cursor
Add to your MCP client config (e.g. `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "observability-demo": {
      "url": "http://localhost:8001/mcp"
    }
  }
}
```

### curl (raw JSON-RPC)
```bash
curl -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: curl-test" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"health_check","arguments":{"service_url":"http://example.com"}},"id":1}'
```

The server automatically identifies clients from the `X-Client-Id` header or User-Agent, and tracks per-client metrics in the dashboard.

## Generate Traffic

```bash
# Install httpx for the scripts
pip3 install httpx

# Run load test (2 min, 5 concurrent workers)
python3 scripts/load_test.py

# Shorter test
python3 scripts/load_test.py --duration 60 --concurrency 3
```

## Chaos Mode (Before/After Demo)

```bash
# Enable chaos: +500ms latency, +25% error rate
python3 scripts/chaos.py on

# Custom chaos
python3 scripts/chaos.py on --latency 1000 --error-rate 0.5

# Disable chaos
python3 scripts/chaos.py off
```

## Demo Flow

1. Start the stack: `docker compose up -d --build`
2. Open Grafana dashboard "MCP Server Observability"
3. Run load test: `python3 scripts/load_test.py --duration 60`
4. Watch metrics populate — tool rates, latency curves, error distribution
5. Enable chaos: `python3 scripts/chaos.py on`
6. Watch dashboards degrade — latency spikes, error rate climbs, alerts fire
7. Open Jaeger UI at http://localhost:16686 → select service `mcp-server`
8. Click a trace to see span details (tool name, duration, error info)
9. Disable chaos: `python3 scripts/chaos.py off`
10. Watch recovery on dashboards

## MCP Tools

| Tool | Description | Simulated Error |
|------|-------------|-----------------|
| `query_database` | DB query with variable latency | ~5% timeout |
| `summarize_text` | LLM summarization | ~3% token limit |
| `analyze_file` | File I/O analysis | ~4% not found |
| `health_check` | HTTP health check | ~8% connection error |
| `calculate_metrics` | Statistical computation | input validation |
| `read_resource` | File resource access | ~6% permission denied |
| `chaos_control` | Toggle chaos mode | — |
