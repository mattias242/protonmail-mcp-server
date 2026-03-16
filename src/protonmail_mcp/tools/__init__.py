from mcp.server.fastmcp import Context

from ..server import AppContext


def _get_app(ctx: Context) -> AppContext:
    """Hämta AppContext från MCP Context."""
    return ctx.request_context.lifespan_context
