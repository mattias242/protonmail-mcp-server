"""Massoperationer: upp till 100 UID:n per anrop, ett kommando med UID-set och resultat per UID."""
import pytest

from protonmail_mcp import imap_bulk
from protonmail_mcp.server import mcp
from protonmail_mcp.tools import labels, manage  # noqa: F401 — registrerar verktygen
from tests.feat_etiketter_bridge import make_client
from tests import sdk_support as sdk

BULK_TOOLS = [
    "mark_email_read", "mark_email_unread", "move_email", "delete_email",
    "add_label", "remove_label", "flag_email", "unflag_email",
]


@pytest.fixture
def imap_and_bridge(monkeypatch):
    return make_client(monkeypatch)


class TestValidateUids:
    def test_keeps_order_and_drops_duplicates(self):
        assert imap_bulk.validate_uids(["5", "1", "5", "9"]) == ["5", "1", "9"]

    def test_empty_list(self):
        with pytest.raises(ValueError, match="minst ett"):
            imap_bulk.validate_uids([])

    def test_at_most_100(self):
        assert len(imap_bulk.validate_uids([str(n) for n in range(1, 101)])) == 100
        with pytest.raises(ValueError, match="högst 100"):
            imap_bulk.validate_uids([str(n) for n in range(1, 102)])

    @pytest.mark.parametrize("bad", ["1:5", "1,2", "*", "", "1\n", "١", " 1", "-1", 5, None])
    def test_every_uid_is_validated(self, bad):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            imap_bulk.validate_uids(["1", bad])

    def test_uid_set(self):
        assert imap_bulk.uid_set(["1", "5", "9"]) == "1,5,9"
        with pytest.raises(ValueError):
            imap_bulk.uid_set(["1", "2:4"])


class TestSetFlags:
    async def test_one_store_for_existing_uids(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        res = await imap_bulk.set_flags(imap, "INBOX", ["1", "5", "9"], r"(\Seen)", add=True)
        assert res["ok"] == ["1", "5"]
        assert res["failed"][0]["uid"] == "9"
        assert bridge.writes() == [("UID STORE", "1,5", "+FLAGS", r"(\Seen)")]

    async def test_nothing_exists_no_write(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        res = await imap_bulk.set_flags(imap, "INBOX", ["1", "2"], r"(\Seen)", add=False)
        assert res["ok"] == []
        assert len(res["failed"]) == 2
        assert bridge.writes() == []

    async def test_refused_store_fails_all(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        bridge.refuse["STORE"] = "backend error"
        res = await imap_bulk.set_flags(imap, "INBOX", ["1", "5"], r"(\Seen)", add=True)
        assert res["ok"] == []
        assert all("backend error" in f["error"] for f in res["failed"])

    async def test_refused_existence_check_raises_before_write(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        bridge.put("INBOX", "1")
        bridge.refuse["FETCH"] = "fetch failed"
        with pytest.raises(ValueError, match="fetch failed"):
            await imap_bulk.set_flags(imap, "INBOX", ["1"], r"(\Seen)", add=True)
        assert bridge.writes() == []


class TestMove:
    async def test_one_uid_move(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        res = await imap_bulk.move(imap, "INBOX", ["1", "5", "7"], "Archive")
        assert res["ok"] == ["1", "5"]
        assert [f["uid"] for f in res["failed"]] == ["7"]
        assert bridge.writes() == [("UID MOVE", "1,5", '"Archive"')]

    @pytest.mark.parametrize("source,target,text", [
        ("Trash", "Trash", "samma mapp"),
        ("Starred", "Trash", "redan ligga i Trash"),
        ("Labels/X", "Drafts", "redan ligga i Drafts"),
        ("All Mail", "Archive", "All Mail"),
        ("INBOX", "Sent", "INBOX och Sent"),
    ])
    async def test_guards_apply_before_contact(self, imap_and_bridge, source, target, text):
        imap, bridge = imap_and_bridge
        bridge.put(source, "1")
        with pytest.raises(ValueError, match=text):
            await imap_bulk.move(imap, source, ["1", "2"], target)
        assert bridge.commands == []

    async def test_refused_move_reports_what_is_left(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        orig = bridge._uid

        async def partly(command, *args):
            if command.upper() == "MOVE":
                # Bridge hann flytta UID 1 innan felet
                bridge.commands.append(("UID MOVE", *args))
                bridge.views["INBOX"].pop("1")
                return type("R", (), {"result": "NO", "lines": [b"backend error"]})()
            return await orig(command, *args)
        bridge.client.uid.side_effect = partly
        res = await imap_bulk.move(imap, "INBOX", ["1", "5"], "Archive")
        assert res["ok"] == ["1"]
        assert [f["uid"] for f in res["failed"]] == ["5"]
        assert "backend error" in res["failed"][0]["error"]


class TestDelete:
    async def test_to_trash_with_uid_move(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        res = await imap_bulk.delete(imap, "INBOX", ["1", "5"], permanent=False)
        assert res == {"ok": ["1", "5"], "failed": []}
        assert bridge.writes() == [("UID MOVE", "1,5", '"Trash"')]

    @pytest.mark.parametrize("mailbox,permanent,text", [
        ("Trash", False, "redan i Trash"),
        ("Starred", False, "redan ligga i Trash"),
        ("Labels/X", False, "redan ligga i Trash"),
        ("All Mail", False, "All Mail"),
        ("INBOX", True, "bara i Trash eller Drafts"),
        ("Labels/X", True, "bara i Trash eller Drafts"),
    ])
    async def test_guards(self, imap_and_bridge, mailbox, permanent, text):
        imap, bridge = imap_and_bridge
        bridge.put(mailbox, "1")
        with pytest.raises(ValueError, match=text):
            await imap_bulk.delete(imap, mailbox, ["1"], permanent=permanent)
        assert bridge.writes() == []

    async def test_permanent_only_given_uids(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5", "6"):
            bridge.put("Trash", uid)
        res = await imap_bulk.delete(imap, "Trash", ["1", "5", "8"], permanent=True)
        assert res["ok"] == ["1", "5"]
        assert [f["uid"] for f in res["failed"]] == ["8"]
        assert bridge.writes() == [
            ("UID STORE", "1,5", "+FLAGS", r"(\Deleted)"),
            ("UID EXPUNGE", "1,5"),
        ]
        assert sorted(bridge.views["Trash"]) == ["6"]
        assert bridge.sent("EXPUNGE") == []

    async def test_refused_expunge_clears_deleted_flag(self, imap_and_bridge):
        imap, bridge = imap_and_bridge
        for uid in ("1", "5"):
            bridge.put("Trash", uid)
        bridge.refuse["EXPUNGE"] = "backend error"
        res = await imap_bulk.delete(imap, "Trash", ["1", "5"], permanent=True)
        assert res["ok"] == []
        assert ("UID STORE", "1,5", "-FLAGS", r"(\Deleted)") in bridge.writes()
        assert bridge.deleted == set()


class TestToolSchema:
    @pytest.mark.parametrize("name", BULK_TOOLS)
    async def test_uid_is_string_or_list_of_at_most_100(self, name, monkeypatch):
        from tests.llm_kontrakt_support import use_test_settings

        use_test_settings(monkeypatch)  # tools/list döljer annars move_email/delete_email utan inställningar
        tools = {t.name: t for t in await mcp.list_tools()}
        schema = sdk.wire(tools[name])["inputSchema"]
        uid = schema["properties"]["uid"]
        variants = uid["anyOf"]
        assert {"type": "string"} in [{"type": v.get("type")} for v in variants]
        array = next(v for v in variants if v.get("type") == "array")
        assert array["items"]["type"] == "string"
        assert array["maxItems"] == 100
        assert array["minItems"] == 1
        assert "mailbox" in schema["required"]

    async def test_json_list_in_string_is_parsed(self, imap_and_bridge, mock_ctx):
        imap, bridge = imap_and_bridge
        mock_ctx.request_context.lifespan_context.imap = imap
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        tool = sdk.get_tool(mcp, "mark_email_read")
        res = await tool.run({"uid": '["1", "5"]', "mailbox": "INBOX"}, context=mock_ctx)
        assert res == {"ok": ["1", "5"], "failed": []}

    async def test_numbers_are_accepted_as_uids(self, imap_and_bridge, mock_ctx):
        imap, bridge = imap_and_bridge
        mock_ctx.request_context.lifespan_context.imap = imap
        for uid in ("1", "5"):
            bridge.put("INBOX", uid)
        tool = sdk.get_tool(mcp, "flag_email")
        assert await tool.run({"uid": [1, 5], "mailbox": "INBOX"}, context=mock_ctx) == {"ok": ["1", "5"], "failed": []}
        assert await tool.run({"uid": 1, "mailbox": "INBOX"}, context=mock_ctx) is True

    async def test_single_string_keeps_bool_return(self, imap_and_bridge, mock_ctx):
        imap, bridge = imap_and_bridge
        mock_ctx.request_context.lifespan_context.imap = imap
        bridge.put("INBOX", "1")
        tool = sdk.get_tool(mcp, "mark_email_read")
        assert await tool.run({"uid": "1", "mailbox": "INBOX"}, context=mock_ctx) is True


@pytest.fixture
def mock_ctx():
    from unittest.mock import MagicMock
    return MagicMock()
