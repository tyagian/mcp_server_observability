"""Stdio wrapper for Claude Desktop integration."""
from tools import mcp_base

if __name__ == "__main__":
    mcp_base.run(transport="stdio")
