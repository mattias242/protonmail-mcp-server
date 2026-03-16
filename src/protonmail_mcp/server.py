import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

# Logging enbart till stderr — stdout är reserverat för MCP stdio-protokollet
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    imap: object  # IMAPClient — undviker tidig import av config
    smtp: object  # SMTPClient


@asynccontextmanager
async def lifespan(server: FastMCP):
    from .config import get_settings
    from .imap_client import IMAPClient
    from .smtp_client import SMTPClient

    try:
        s = get_settings()
    except Exception as exc:
        logger.error(
            "Kunde inte läsa konfiguration: %s\n"
            "Kontrollera att PROTONMAIL_USERNAME och PROTONMAIL_PASSWORD är satta "
            "(miljövariabler eller .env-fil).",
            exc,
        )
        raise

    imap = IMAPClient(s)
    try:
        yield AppContext(imap=imap, smtp=SMTPClient(s))
    finally:
        await imap.disconnect()


mcp = FastMCP("protonmail", lifespan=lifespan)


def main() -> None:
    # Importera tools här för att undvika cirkulär import vid modulnivå
    from .tools import mailboxes, messages, search, manage, send  # noqa: F401

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
