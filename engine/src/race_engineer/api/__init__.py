"""FastAPI app: REST endpoints for each analysis tool, a health check, the chat endpoint and the
MCP server mounted at /mcp, all built from the shared tool registry (`tools.registry`).

Rate limits, the daily spending cap and deployment come in M7.
"""
