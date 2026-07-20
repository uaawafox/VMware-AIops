"""MCP tool modules.

Each module registers its ``@mcp.tool()`` functions onto the shared ``mcp``
instance from ``vmware_aiops.mcp_server._shared``. Importing the modules (done by
``vmware_aiops.mcp_server.server``) is what wires the tools into the server.
"""
