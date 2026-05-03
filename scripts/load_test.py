#!/usr/bin/env python3
"""
Load test script for the MCP server.

Uses FastMCP client to properly communicate with the streamable-http transport.
Run separately: python scripts/load_test.py [--duration 120] [--concurrency 5]
"""

import argparse
import asyncio
import random
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import httpx

MCP_URL = "http://localhost:8001/mcp"

TOOL_CALLS = [
    ("query_database", {"query": "SELECT * FROM users WHERE active = true", "limit": 50}),
    ("query_database", {"query": "SELECT id name email FROM orders JOIN products ON oid = pid WHERE total > 100", "limit": 25}),
    ("summarize_text", {"text": "Monitoring MCP servers in production requires a comprehensive observability strategy covering metrics traces and logs. This document explores best practices for instrumenting tool calls tracking protocol messages and building effective Grafana dashboards for real-time visibility into your MCP ecosystem."}),
    ("summarize_text", {"text": "The Model Context Protocol enables AI clients to invoke tools execute operations and retrieve resources through a standardized interface. Production deployments need careful monitoring of latency error rates and resource utilization."}),
    ("analyze_file", {"filename": "src/main.py"}),
    ("analyze_file", {"filename": "config/settings.yaml"}),
    ("analyze_file", {"filename": "data/users.csv"}),
    ("health_check", {"service_url": "http://api.example.com/health"}),
    ("health_check", {"service_url": "http://db.internal:5432/health"}),
    ("calculate_metrics", {"values": [random.uniform(0, 100) for _ in range(20)]}),
    ("read_resource", {"path": "/etc/config/app.yaml"}),
    ("read_resource", {"path": "/var/data/report.csv"}),
    ("read_resource", {"path": "/tmp/cache/model.bin"}),
]


async def make_call(worker_id: int, results: dict):
    """Single worker that makes calls in a loop, reconnecting on failure."""
    while not results["_stop"]:
        try:
            transport = StreamableHttpTransport(
                MCP_URL,
                headers={"X-Client-Id": f"load-test-worker-{worker_id}"},
            )
            async with Client(transport=transport) as client:
                while not results["_stop"]:
                    tool_name, arguments = random.choice(TOOL_CALLS)
                    start = time.time()
                    try:
                        await client.call_tool(tool_name, arguments)
                        dur = time.time() - start
                        results["success"] += 1
                        results["by_tool"].setdefault(tool_name, {"calls": 0, "errors": 0, "durations": []})
                        results["by_tool"][tool_name]["calls"] += 1
                        results["by_tool"][tool_name]["durations"].append(dur)
                    except Exception as e:
                        dur = time.time() - start
                        results["error"] += 1
                        results["by_tool"].setdefault(tool_name, {"calls": 0, "errors": 0, "durations": []})
                        results["by_tool"][tool_name]["calls"] += 1
                        results["by_tool"][tool_name]["errors"] += 1
                        results["by_tool"][tool_name]["durations"].append(dur)
                        # If connection-level error, break to reconnect
                        if "connect" in str(e).lower() or "closed" in str(e).lower():
                            break
                    results["total"] += 1
                    await asyncio.sleep(random.uniform(0.05, 0.3))
        except Exception:
            # Connection failed, wait briefly and retry
            await asyncio.sleep(1)


async def run_load_test(duration_seconds: int, concurrency: int):
    print(f"🚀 Starting load test: {duration_seconds}s duration, {concurrency} workers")
    print(f"   Target: {MCP_URL}")
    print()

    results = {"total": 0, "success": 0, "error": 0, "by_tool": {}, "_stop": False}

    # Start workers
    tasks = [asyncio.create_task(make_call(i, results)) for i in range(concurrency)]

    # Progress reporting
    start_time = time.time()
    while time.time() - start_time < duration_seconds:
        await asyncio.sleep(10)
        elapsed = int(time.time() - start_time)
        print(f"   [{elapsed}s] {results['total']} calls, {results['success']} ok, {results['error']} errors")

    # Stop workers
    results["_stop"] = True
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Summary
    elapsed = round(time.time() - start_time, 1)
    print()
    print(f"✅ Load test complete in {elapsed}s")
    print(f"   Total calls:  {results['total']}")
    print(f"   Successful:   {results['success']}")
    print(f"   Errors:       {results['error']}")
    if results["total"] > 0:
        print(f"   Error rate:   {results['error']/results['total']*100:.1f}%")
        print(f"   Throughput:   {results['total']/elapsed:.1f} calls/sec")
    print()
    print("   Per-tool breakdown:")
    for tool, stats in sorted(results["by_tool"].items()):
        durations = stats["durations"]
        avg = sum(durations) / len(durations) if durations else 0
        p95 = sorted(durations)[int(len(durations) * 0.95)] if len(durations) > 1 else avg
        print(f"     {tool:20s}  calls={stats['calls']:4d}  errors={stats['errors']:3d}  avg={avg:.3f}s  p95={p95:.3f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Server Load Test")
    parser.add_argument("--duration", type=int, default=120, help="Test duration in seconds")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers")
    args = parser.parse_args()
    asyncio.run(run_load_test(args.duration, args.concurrency))
