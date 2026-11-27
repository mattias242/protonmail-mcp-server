"""Begränsningar av vad servern (och därmed en LLM) får göra med kontot.

Inställningarna (PROTONMAIL_READ_ONLY m.fl., se config.py) kontrolleras
centralt, så att inget verktyg kan glömma dem:

- IMAPClient-metoderna och modulfunktionerna som skriver (flaggor, flytt,
  radering, etiketter, mappar, utkast) är dekorerade med ``write_operation``,
  som vägrar innan Bridge kontaktas.
- SMTPClient kontrollerar skickpolicyn i den gemensamma leveransvägen.
- MCP-servern (server.py) vägrar dessutom verktyg efter namn innan de körs,
  så att ett verktyg som först läser originalet (t.ex. reply_to_email) inte
  ens läser när skicket ändå skulle vägras.

Alla vägringar är ValueError med en text som når klienten oförändrad.
"""
from __future__ import annotations

import functools
import inspect
import math
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable

from . import i18n
from . import audit
from .config import normalize_domain, parse_allowed_recipients

# Verktygen per kategori. Används av MCP-servern för att vägra tidigt; den
# egentliga spärren sitter i klienterna. Ett verktyg som saknas här
# släpps igenom till klienternas spärrar (tests/unit/test_llm_skick_skrivskydd.py
# kräver att varje registrerat verktyg finns i sin lista där).
WRITE_TOOLS = frozenset({
    "mark_email_read",
    "mark_email_unread",
    "move_email",
    "delete_email",
    "create_folder",
    "delete_folder",
    "rename_folder",
    "add_label",
    "remove_label",
    "flag_email",
    "unflag_email",
})
DRAFT_TOOLS = frozenset({
    "create_draft",
    "create_reply_draft",
    "create_forward_draft",
    "update_draft",
})
SEND_TOOLS = frozenset({
    "send_email",
    "reply_to_email",
    "forward_email",
    "send_draft",
})
# Verktyg som flyttar, raderar, byter namn på eller ersätter något: alla med
# destructiveHint utom skicken, som PROTONMAIL_SEND_MODE styr. Vägras med
# PROTONMAIL_ALLOW_DESTRUCTIVE=false (tests/unit/test_llm_skick_destruktivt.py
# kräver att listan stämmer med verktygens klassning).
DESTRUCTIVE_TOOLS = frozenset({
    "move_email",
    "delete_email",
    "delete_folder",
    "rename_folder",
    "remove_label",
    "update_draft",
})


def _read_only(settings: Any) -> bool:
    # "is True": en attrapp (MagicMock) som settings ska inte räknas som påslagen
    return getattr(settings, "read_only", False) is True


def require_write(settings: Any, action: str) -> None:
    """ValueError om kontot är skrivskyddat (PROTONMAIL_READ_ONLY=true)."""
    if _read_only(settings):
        raise ValueError(
            i18n.t(
                'Kontot är skrivskyddat för den här servern (PROTONMAIL_READ_ONLY=true), så {action} '
                'vägras. Inget har ändrats eller skickats.',
                action=action,
            )
        )


_DRAFT_HINT = (
    "Skapa ett utkast i stället med create_draft, create_reply_draft eller "
    "create_forward_draft, så kan användaren granska och skicka det själv i Proton."
)


def _send_mode(settings: Any) -> str:
    mode = getattr(settings, "send_mode", "send")
    return mode if mode in ("send", "draft_only", "disabled") else "send"


def require_send(settings: Any, action: str) -> None:
    """ValueError om skick inte är tillåtet (skrivskydd eller PROTONMAIL_SEND_MODE)."""
    require_write(settings, action)
    mode = _send_mode(settings)
    if mode == "draft_only":
        raise ValueError(
            i18n.t(
                'Servern får inte skicka e-post (PROTONMAIL_SEND_MODE=draft_only), så {action} vägras '
                'och inget skickades. {DRAFT_HINT}',
                action=action,
                DRAFT_HINT=i18n.t(_DRAFT_HINT),
            )
        )
    if mode == "disabled":
        raise ValueError(
            i18n.t(
                'Servern får varken skicka e-post eller skapa utkast (PROTONMAIL_SEND_MODE=disabled), '
                'så {action} vägras och inget skickades.',
                action=action,
            )
        )


def require_draft(settings: Any, action: str) -> None:
    """ValueError om utkast inte får skapas eller ändras (skrivskydd eller disabled)."""
    require_write(settings, action)
    if _send_mode(settings) == "disabled":
        raise ValueError(
            i18n.t(
                'Servern får varken skicka e-post eller skapa utkast (PROTONMAIL_SEND_MODE=disabled), '
                'så {action} vägras. Inget har sparats.',
                action=action,
            )
        )


def _allow_destructive(settings: Any) -> bool:
    # "is False": en attrapp (MagicMock) som settings ska inte räknas som avstängd
    return getattr(settings, "allow_destructive", True) is not False


def _refuse_destructive(settings: Any, action: str) -> None:
    """ValueError med PROTONMAIL_ALLOW_DESTRUCTIVE=false (skrivskyddet kontrolleras separat)."""
    if not _allow_destructive(settings):
        raise ValueError(
            i18n.t(
                'Servern får inte flytta, radera, byta namn på eller ersätta något i kontot '
                '(PROTONMAIL_ALLOW_DESTRUCTIVE=false), så {action} vägras. Inget har ändrats. Läsa, '
                'markera, stjärnmärka, lägga till etiketter och skapa nya utkast (create_draft, '
                'create_reply_draft, create_forward_draft) går bra; be användaren göra ändringen själv '
                'i Proton.',
                action=action,
            )
        )


def check_tool(settings: Any, tool: str) -> None:
    """Vägra ett verktyg efter namn, innan det körs (anropas av MCP-servern)."""
    if tool in SEND_TOOLS:
        require_send(settings, tool)
    elif tool in DRAFT_TOOLS:
        require_draft(settings, tool)
    elif tool in WRITE_TOOLS:
        require_write(settings, tool)
    if tool in DESTRUCTIVE_TOOLS:
        _refuse_destructive(settings, tool)


def _is_allowed(address: str, rules: tuple[tuple[str, str], ...]) -> bool:
    local, _, domain = address.rpartition("@")
    normalized = normalize_domain(domain)
    if not local or normalized is None:
        return False
    return ("", normalized) in rules or (local, normalized) in rules


def check_recipients(settings: Any, recipients: list[str]) -> None:
    """ValueError om någon mottagare inte är tillåten. Inget skickas då."""
    raw = getattr(settings, "allowed_recipients", "")
    rules = parse_allowed_recipients(raw) if isinstance(raw, str) else ()
    if not rules:
        return
    refused: list[str] = []
    for address in recipients:
        if not _is_allowed(address, rules) and address not in refused:
            refused.append(address)
    if refused:
        raise ValueError(
            i18n.t(
                'Skicket vägrades: {join} finns inte bland de tillåtna mottagarna '
                '(PROTONMAIL_ALLOWED_RECIPIENTS). Inget mejl skickades till någon.',
                join=', '.join(refused),
            )
        )


def _limit(settings: Any, name: str) -> int:
    value = getattr(settings, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def check_recipient_count(settings: Any, recipients: list[str]) -> None:
    """ValueError om skicket har fler unika mottagare än PROTONMAIL_MAX_RECIPIENTS."""
    limit = _limit(settings, "max_recipients")
    count = len({address.lower() for address in recipients})
    if limit and count > limit:
        raise ValueError(
            i18n.t(
                'Skicket vägrades: {count} mottagare, men högst {limit} per mejl är tillåtet '
                '(PROTONMAIL_MAX_RECIPIENTS). Inget mejl skickades.',
                count=count,
                limit=limit,
            )
        )


_WINDOW = 3600.0


def _wait_text(seconds: float) -> str:
    if seconds >= 60:
        minutes = math.ceil(seconds / 60)
        if minutes == 1:
            return i18n.t("om {minutes} minut", minutes=minutes)
        return i18n.t("om {minutes} minuter", minutes=minutes)
    whole = max(1, math.ceil(seconds))
    if whole == 1:
        return i18n.t("om {whole} sekund", whole=whole)
    return i18n.t("om {whole} sekunder", whole=whole)


class SendRateLimiter:
    """Högst PROTONMAIL_MAX_SENDS_PER_HOUR skick under en glidande timme.

    Räknaren ligger i minnet. ``acquire`` kontrollerar och räknar skicket i
    ett svep (utan await emellan, så samtidiga anrop kan inte smita förbi).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._times: deque[float] = deque()

    def acquire(self, settings: Any) -> None:
        limit = _limit(settings, "max_sends_per_hour")
        if not limit:
            return
        now = self._clock()
        while self._times and now - self._times[0] >= _WINDOW:
            self._times.popleft()
        if len(self._times) >= limit:
            wait = self._times[-limit] + _WINDOW - now
            at = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M:%S")
            raise ValueError(
                i18n.t(
                    'Skicket vägrades: högst {limit} skick per timme är tillåtet '
                    '(PROTONMAIL_MAX_SENDS_PER_HOUR), och gränsen är nådd. Nästa skick går {wait_text} '
                    '(kl. {at}). Inget mejl skickades.',
                    limit=limit,
                    wait_text=_wait_text(wait),
                    at=at,
                )
            )
        self._times.append(now)


# Argument som får hamna i granskningsloggen, och fältet de skrivs till.
# Allt annat (t.ex. meddelandet i ett utkast) utelämnas.
_AUDIT_ARGS = {
    "mailbox": "mailbox",
    "name": "mailbox",
    "old_name": "mailbox",
    "target": "target",
    "new_name": "target",
    "label": "label",
    "uid": "uids",
    "old_uid": "uids",
    "uids": "uids",
    "permanent": "permanent",
    "force": "force",
    "flags": "flags",
    "add": "add",
    "starred": "starred",
    "message_id": "message_id",
}


def _audit_fields(bound: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for arg, value in bound.items():
        key = _AUDIT_ARGS.get(arg)
        if key is None or value is None:
            continue
        if key == "uids":
            value = [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
        elif not isinstance(value, (str, bool, int)):
            value = str(value)
        fields[key] = value
    return fields


def _result_fields(value: Any) -> tuple[str, dict[str, Any]]:
    """("ok"/"error", extra fält) ur skrivfunktionens returvärde."""
    if isinstance(value, dict) and "ok" in value and "failed" in value:
        failed = [str(f.get("uid")) for f in value["failed"] if isinstance(f, dict)]
        return ("error" if failed else "ok"), {"ok_uids": list(value["ok"]), "failed_uids": failed}
    if isinstance(value, str):
        return "ok", {"new_uid": value}
    return "ok", {}


def write_operation(
    action: str, *, draft: bool = False, destructive: bool = False, mailbox: str | None = None
):
    """Dekorator för IMAP-funktioner som skriver i kontot.

    Den dekorerade funktionens första argument är IMAPClient (``self`` eller
    ``imap``); policyn läses ur dess ``_settings``. Kontrollen görs innan
    funktionen körs, alltså innan något valideras eller skickas till Bridge.
    Varje anrop, även ett vägrat, ger en rad i granskningsloggen (audit.py)
    med åtgärd, mapp, UID:n och resultat — aldrig meddelandeinnehåll.
    ``draft=True`` för utkast (skickläget gäller), ``destructive=True`` för
    det som flyttar, raderar, byter namn på eller ersätter något
    (PROTONMAIL_ALLOW_DESTRUCTIVE gäller).
    ``mailbox`` anger mappen för funktioner som inte tar den som argument.
    Lägg dekoratorn närmast funktionen (under ``_serialized``), så att även
    anrop via ``__wrapped__`` (utan lås) kontrolleras.
    """
    base = require_draft if draft else require_write

    def check(settings: Any, action: str) -> None:
        base(settings, action)
        if destructive:
            _refuse_destructive(settings, action)

    def decorate(func):
        signature = inspect.signature(func)

        @functools.wraps(func)
        async def wrapper(imap, *args, **kwargs):
            settings = getattr(imap, "_settings", None)
            try:
                bound = signature.bind(imap, *args, **kwargs).arguments
            except TypeError:
                bound = {}
            fields = _audit_fields(bound)
            if mailbox is not None:
                fields.setdefault("mailbox", mailbox)
            try:
                check(settings, action)
                value = await func(imap, *args, **kwargs)
            except BaseException as exc:
                audit.record(settings, action, result="error", error=exc, **fields)
                raise
            result, extra = _result_fields(value)
            audit.record(settings, action, result=result, **fields, **extra)
            return value
        return wrapper
    return decorate
