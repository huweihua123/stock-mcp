"""
Temporary monkeypatch to handle RuntimeError during initialization.

This is a workaround for the MCP SSE initialization issue:
https://github.com/modelcontextprotocol/python-sdk/issues/423

This patch prevents the server from crashing when a POST message is received
before initialization is complete (e.g., after a server reload or client reconnect).

WARNING: This is a temporary solution and should be removed once the upstream
issue is fixed in the MCP SDK.
"""

from mcp.server.session import ServerSession

# Store original method
# pylint: disable-next=protected-access
_original_received_request = ServerSession._received_request


async def _patched_received_request(self, *args, **kwargs):
    """Patched version that silently ignores initialization errors."""
    try:
        return await _original_received_request(self, *args, **kwargs)
    except RuntimeError as e:
        if "initialization was complete" in str(e):
            # Silently ignore initialization timing errors
            # The client should retry or reinitialize
            pass
        else:
            # Re-raise other RuntimeErrors
            raise


# Apply monkey patch
# pylint: disable-next=protected-access
ServerSession._received_request = _patched_received_request
