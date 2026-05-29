"""C.H.A.R.L.I.E. — MCP (Model Context Protocol) Client Infrastructure"""

from charlie.mcp.bridge import MCPToolBridge
from charlie.mcp.client import MCPClient
from charlie.mcp.manager import MCPManager

__all__ = ["MCPClient", "MCPManager", "MCPToolBridge"]
