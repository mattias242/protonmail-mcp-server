"""HTTP-läget: servern över Streamable HTTP, skyddad av en API-nyckel.

För klienter som inte kan starta ett lokalt program (Perplexity). Tänkt att
köras bakom Cloudflare Tunnel och Cloudflare Access: servern lyssnar som
standard bara på 127.0.0.1 och kräver ändå en egen nyckel i varje anrop, så
att ingenting når brevlådan om Access-regeln saknas eller är fel (fail-closed).
"""
import hmac
import logging
from contextlib import asynccontextmanager

from . import i18n
from ._sdk import MCPServer, TransportSecuritySettings

logger = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 32
MCP_PATH = "/mcp"

# Som SDK:ns eget skydd mot DNS rebinding på localhost; publika värdnamn läggs till
_LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

_DENIED_BODY = b'{"error": "API-nyckel saknas eller \\u00e4r fel."}'


def require_token(settings) -> str:
    """API-nyckeln ur PROTONMAIL_HTTP_TOKEN, eller ValueError om den saknas eller är för kort."""
    token = settings.http_token.get_secret_value() if settings.http_token is not None else ""
    if not token:
        raise ValueError(
            i18n.t(
                'HTTP-läget kräver PROTONMAIL_HTTP_TOKEN: API-nyckeln som varje anrop måste ha, minst '
                '{MIN_TOKEN_LENGTH} tecken. Skapa en med t.ex. `openssl rand -hex 32`.',
                MIN_TOKEN_LENGTH=MIN_TOKEN_LENGTH,
            )
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            i18n.t(
                'PROTONMAIL_HTTP_TOKEN måste vara minst {MIN_TOKEN_LENGTH} tecken (den är {len}). '
                'Skapa en med t.ex. `openssl rand -hex 32`.',
                MIN_TOKEN_LENGTH=MIN_TOKEN_LENGTH,
                len=len(token),
            )
        )
    return token


def parse_allowed_hosts(value: str) -> list[str]:
    """PROTONMAIL_HTTP_ALLOWED_HOSTS som en lista med värdnamn (gemener)."""
    hosts = []
    for entry in value.split(","):
        host = entry.strip().lower()
        if not host:
            continue
        if "*" in host or "/" in host or ":" in host or " " in host:
            raise ValueError(
                i18n.t(
                    'Ogiltigt värdnamn i PROTONMAIL_HTTP_ALLOWED_HOSTS: {strip!r}. Ange bara värdnamn, '
                    't.ex. protonmail-mcp.exempel.se (utan https://, port eller *).',
                    strip=entry.strip(),
                )
            )
        hosts.append(host)
    return hosts


class ApiKeyProtected:
    """ASGI-app som släpper igenom ett anrop bara med rätt API-nyckel.

    Nyckeln tas ur "Authorization: Bearer <nyckel>" eller "X-API-Key: <nyckel>"
    och jämförs i konstant tid. Ett nekat anrop får 401 utan att nå MCP-servern;
    loggen visar vilka rubriker som fanns, aldrig deras värden.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan" or self._authorized(scope):
            await self.app(scope, receive, send)
            return
        names = sorted({name.decode("latin-1").lower() for name, _ in scope.get("headers", [])})
        logger.warning(
            "Nekade %s %s utan giltig API-nyckel (rubriker: %s)",
            scope.get("method", scope["type"]), scope.get("path", ""), ", ".join(names),
        )
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(_DENIED_BODY)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": _DENIED_BODY})

    def _authorized(self, scope) -> bool:
        ok = False
        for name, value in scope.get("headers", []):
            name = name.lower()
            if name == b"authorization":
                scheme, _, candidate = value.partition(b" ")
                if scheme.lower() != b"bearer":
                    continue
            elif name == b"x-api-key":
                candidate = value
            else:
                continue
            # Alla kandidater jämförs, så att tiden inte avslöjar vilken som fanns
            ok = hmac.compare_digest(candidate.strip(), self._token) or ok
        return ok

    @asynccontextmanager
    async def lifespan(self):
        """Kör den inre appens lifespan (sessionshanteraren och serverns lifespan)."""
        async with self.app.router.lifespan_context(self.app):
            yield


def build_app(server: MCPServer, settings) -> ApiKeyProtected:
    """Streamable HTTP-appen på /mcp bakom API-nyckeln. ValueError om inställningarna inte duger."""
    token = require_token(settings)
    hosts = parse_allowed_hosts(settings.http_allowed_hosts)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_LOCAL_HOSTS + hosts + [f"{host}:*" for host in hosts],
        allowed_origins=_LOCAL_ORIGINS + [f"https://{host}" for host in hosts],
    )
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH, transport_security=security, host=settings.http_host
    )
    return ApiKeyProtected(app, token)


def serve(app: ApiKeyProtected, settings) -> None:
    logger.info("HTTP-läget lyssnar på http://%s:%d%s", settings.http_host, settings.http_port, MCP_PATH)
    _serve(app, settings.http_host, settings.http_port)


def _serve(app, host: str, port: int) -> None:  # pragma: no cover - startar uvicorn på riktigt
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
