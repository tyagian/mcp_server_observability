#!/usr/bin/env python3
"""
Chaos control script for the MCP server.

Usage:
  python scripts/chaos.py on                     # Enable chaos (defaults)
  python scripts/chaos.py on --latency 1000      # 1s extra latency
  python scripts/chaos.py on --error-rate 0.5    # 50% error boost
  python scripts/chaos.py off                    # Disable chaos
"""

import argparse
import asyncio
import json
import sys

from fastmcp import Client

MCP_URL = "http://localhost:8001/mcp"


async def toggle_chaos(enabled: bool, latency_ms: int, error_rate: float):
    try:
        async with Client(MCP_URL) as client:
            result = await client.call_tool(
                "chaos_control",
                {
                    "enabled": enabled,
                    "extra_latency_ms": latency_ms,
                    "error_rate_boost": error_rate,
                },
            )

            # Parse the result — handle different FastMCP return formats
            if hasattr(result, "content"):
                items = result.content
            elif hasattr(result, "__iter__"):
                items = result
            else:
                items = [result]

            for item in items:
                text = getattr(item, "text", None) or str(item)
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if data.get("chaos_enabled"):
                    print(f"🔥 Chaos ENABLED")
                    print(f"   Extra latency: {data['extra_latency_ms']}ms")
                    print(f"   Error rate boost: {data['error_rate_boost']*100:.0f}%")
                else:
                    print(f"✅ Chaos DISABLED — tools back to normal")
                return

            print(f"Response: {result}")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("   Make sure the server is running: docker compose up -d")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Chaos Control")
    parser.add_argument("action", choices=["on", "off"], help="Enable or disable chaos")
    parser.add_argument("--latency", type=int, default=500, help="Extra latency in ms")
    parser.add_argument("--error-rate", type=float, default=0.25, help="Error rate boost (0-1)")
    args = parser.parse_args()

    asyncio.run(
        toggle_chaos(
            enabled=(args.action == "on"),
            latency_ms=args.latency,
            error_rate=args.error_rate,
        )
    )
