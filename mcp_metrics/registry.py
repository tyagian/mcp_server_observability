"""
Central Prometheus metrics registry for MCP tool observability.

Metric layers:
  1. Transport       — handshake, connection, reconnection, transport type
  2. Protocol        — messages, sizes, version distribution, JSON-RPC errors
  3. Tool execution  — call count, duration, errors, result size, concurrency
  4. Agentic         — prompt templates, token usage, hallucination tracking
  5. Session         — active sessions, duration, client identification
  6. Resource access — frequency, size, errors, anomaly signals
  7. System          — CPU, memory gauges
  8. Rate limiting   — throttle events
"""

from prometheus_client import Counter, Gauge, Histogram, Summary


class MCPMetricsRegistry:
    def __init__(self) -> None:
        # ══════════════════════════════════════════════════════════════════
        # 1. TRANSPORT LAYER METRICS
        # ══════════════════════════════════════════════════════════════════
        self.handshake_total = Counter(
            "mcp_handshake_total",
            "Total handshake attempts",
            ["status", "transport"],  # status: success|failure
        )
        self.connection_total = Counter(
            "mcp_connection_total",
            "Total connections by transport type",
            ["transport", "client_id"],
        )
        self.reconnection_total = Counter(
            "mcp_reconnection_total",
            "Total reconnection attempts",
            ["client_id", "reason"],
        )
        self.transport_type_active = Gauge(
            "mcp_transport_type_active",
            "Active connections by transport type",
            ["transport"],
        )

        # ══════════════════════════════════════════════════════════════════
        # 2. PROTOCOL MESSAGE METRICS
        # ══════════════════════════════════════════════════════════════════
        self.messages_received_total = Counter(
            "mcp_messages_received_total",
            "Total MCP messages received",
            ["msg_type", "client_id"],
        )
        self.messages_sent_total = Counter(
            "mcp_messages_sent_total",
            "Total MCP messages sent",
            ["msg_type"],
        )
        self.message_size_bytes = Histogram(
            "mcp_message_size_bytes",
            "MCP message payload size",
            ["direction"],
            buckets=(64, 256, 1024, 4096, 16384, 65536, 262144),
        )
        self.protocol_version_count = Counter(
            "mcp_protocol_version_count",
            "Protocol version distribution",
            ["version", "client_id"],
        )
        self.jsonrpc_errors_total = Counter(
            "mcp_jsonrpc_errors_total",
            "JSON-RPC protocol errors by code",
            ["error_code", "error_message"],
        )
        self.message_latency_seconds = Histogram(
            "mcp_message_latency_seconds",
            "Request-response latency (p50/p90/p99)",
            ["msg_type"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10),
        )

        # ══════════════════════════════════════════════════════════════════
        # 3. TOOL EXECUTION METRICS
        # ══════════════════════════════════════════════════════════════════
        self.tool_calls_total = Counter(
            "mcp_tool_calls_total",
            "Total MCP tool invocations",
            ["tool_name", "status", "client_id"],
        )
        self.tool_duration_seconds = Histogram(
            "mcp_tool_duration_seconds",
            "Tool execution duration in seconds",
            ["tool_name"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        )
        self.tool_duration_quantiles = Summary(
            "mcp_tool_duration_quantiles",
            "Tool execution time quantiles",
            ["tool_name"],
        )
        self.tool_errors_total = Counter(
            "mcp_tool_errors_total",
            "Tool errors by classification",
            ["tool_name", "error_type"],
        )
        self.tool_result_size_bytes = Histogram(
            "mcp_tool_result_size_bytes",
            "Size of tool result payloads in bytes",
            ["tool_name"],
            buckets=(64, 256, 1024, 4096, 16384, 65536, 262144),
        )
        self.tool_invalid_params_total = Counter(
            "mcp_tool_invalid_params_total",
            "Tool calls with invalid parameters (parameter hallucination)",
            ["tool_name"],
        )
        self.tool_inflight = Gauge(
            "mcp_tool_inflight",
            "Concurrent in-flight tool calls",
            ["tool_name"],
        )
        self.tool_inflight_total = Gauge(
            "mcp_tool_inflight_total",
            "Total concurrent in-flight tool calls across all tools",
        )
        self.tool_retries_total = Counter(
            "mcp_tool_retries_total",
            "Tool retry attempts (self-correction)",
            ["tool_name", "attempt_number"],
        )

        # ══════════════════════════════════════════════════════════════════
        # 4. AGENTIC / LLM METRICS
        # ══════════════════════════════════════════════════════════════════
        self.prompt_template_usage_total = Counter(
            "mcp_prompt_template_usage_total",
            "Prompt template invocations",
            ["prompt_name", "client_id"],
        )
        self.token_usage_total = Counter(
            "mcp_token_usage_total",
            "Token consumption per tool call",
            ["tool_name", "direction"],  # direction: input|output
        )
        self.token_usage_per_call = Histogram(
            "mcp_token_usage_per_call",
            "Token count distribution per tool call",
            ["tool_name"],
            buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
        )
        self.tool_hallucination_total = Counter(
            "mcp_tool_hallucination_total",
            "Calls to non-existent tools (hallucination detection)",
            ["attempted_tool", "client_id"],
        )
        self.task_success_total = Counter(
            "mcp_task_success_total",
            "Task completion success/failure",
            ["status"],  # success|failure
        )
        self.turns_to_completion = Histogram(
            "mcp_turns_to_completion",
            "Conversation turns to complete a task",
            buckets=(1, 2, 3, 5, 7, 10, 15, 20),
        )
        self.self_correction_total = Counter(
            "mcp_self_correction_total",
            "Autonomous error recovery attempts",
            ["tool_name", "status"],  # status: recovered|failed
        )

        # ══════════════════════════════════════════════════════════════════
        # 5. SESSION METRICS
        # ══════════════════════════════════════════════════════════════════
        self.active_sessions = Gauge(
            "mcp_active_sessions",
            "Currently active MCP sessions",
        )
        self.active_sessions_by_client = Gauge(
            "mcp_active_sessions_by_client",
            "Active sessions per client",
            ["client_id"],
        )
        self.sessions_total = Counter(
            "mcp_sessions_total",
            "Total MCP sessions created",
            ["client_id", "transport"],
        )
        self.session_duration_seconds = Histogram(
            "mcp_session_duration_seconds",
            "MCP session duration in seconds",
            buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
        )
        self.session_disconnects_total = Counter(
            "mcp_session_disconnects_total",
            "Total session disconnects",
            ["client_id", "reason"],
        )

        # ══════════════════════════════════════════════════════════════════
        # 6. RESOURCE ACCESS METRICS
        # ══════════════════════════════════════════════════════════════════
        self.resource_accessed_total = Counter(
            "mcp_resource_accessed_total",
            "Resource access count",
            ["resource", "client_id"],
        )
        self.resource_size_bytes = Histogram(
            "mcp_resource_size_bytes",
            "Resource response size",
            ["resource"],
            buckets=(256, 1024, 4096, 16384, 65536, 262144),
        )
        self.resource_errors_total = Counter(
            "mcp_resource_errors_total",
            "Resource access errors",
            ["resource", "error_type"],
        )
        self.resource_access_anomaly_total = Counter(
            "mcp_resource_access_anomaly_total",
            "Anomalous resource access patterns detected",
            ["resource", "anomaly_type"],
        )

        # ══════════════════════════════════════════════════════════════════
        # 7. SYSTEM RESOURCE GAUGES
        # ══════════════════════════════════════════════════════════════════
        self.cpu_usage_percent = Gauge("mcp_cpu_usage_percent", "CPU usage %")
        self.memory_usage_mb = Gauge("mcp_memory_usage_mb", "Memory usage MB")

        # ══════════════════════════════════════════════════════════════════
        # 8. RATE LIMITING / THROTTLING
        # ══════════════════════════════════════════════════════════════════
        self.rate_limit_total = Counter(
            "mcp_rate_limit_total",
            "Rate limit / throttle events",
            ["client_id", "tool_name"],
        )
        self.rate_limit_active = Gauge(
            "mcp_rate_limit_active",
            "Currently rate-limited clients",
        )

        # ── Custom tool-specific metrics ─────────────────────────────────
        self._custom: dict = {}

    def register(self, name: str, metric):
        self._custom[name] = metric

    def get(self, name: str):
        return self._custom.get(name)


metrics_registry = MCPMetricsRegistry()
