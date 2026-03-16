from mcp.server.fastmcp import Context

from ..server import mcp
from . import _get_app


@mcp.tool()
async def create_folder(ctx: Context, name: str) -> bool:
    """Skapa en ny mapp i brevlådan.

    Args:
        name: Namn på den nya mappen
    """
    app = _get_app(ctx)
    return await app.imap.create_folder(name)


@mcp.tool()
async def delete_folder(ctx: Context, name: str) -> bool:
    """Ta bort en mapp (måste vara tom).

    Args:
        name: Namn på mappen att ta bort
    """
    app = _get_app(ctx)
    return await app.imap.delete_folder(name)


@mcp.tool()
async def rename_folder(ctx: Context, old_name: str, new_name: str) -> bool:
    """Byt namn på en mapp.

    Args:
        old_name: Nuvarande namn
        new_name: Nytt namn
    """
    app = _get_app(ctx)
    return await app.imap.rename_folder(old_name, new_name)
