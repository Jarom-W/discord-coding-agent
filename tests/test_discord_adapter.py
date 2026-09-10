import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discord_coding_agent.delivery import Delivery
from discord_coding_agent.discord_client import BridgeClient, DiscordTransport
from discord_coding_agent.engine import HELP, Pending
from discord_coding_agent.state import StateStore
from discord_coding_agent.workspaces import WORKSPACE_HELP


async def test_large_result_reaches_discord_inline_with_unicode_and_no_files():
    content = "Changes — café, 中文 and 😀 @everyone\n" * 1000
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=9)))
    transport = DiscordTransport(SimpleNamespace())
    transport.channel = AsyncMock(return_value=channel)
    transport.find = AsyncMock(return_value=None)
    completed = []
    delivery = Delivery(transport, 1, lambda _: True, lambda *_: True, completed.append)
    delivery.start()
    try:
        delivery.text(content, result_id="result")
        await delivery.queue.join()
    finally:
        await delivery.close()
    calls = channel.send.await_args_list
    assert len(calls) > 8
    assert "".join(call.args[0].rpartition("\n")[0] for call in calls) == content
    assert completed == ["result"]
    for call in calls:
        assert "file" not in call.kwargs and "files" not in call.kwargs
        assert call.kwargs["allowed_mentions"].everyone is False
        assert len(call.args[0].encode("utf-16-le")) // 2 <= 2000
        wire = discord.utils._to_json({"content": call.args[0]})
        assert json.loads(wire.encode("utf-8"))["content"] == call.args[0]


async def test_buttons_styles_ids_and_no_mentions():
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=9)))
    transport = DiscordTransport(SimpleNamespace())
    transport.channel = AsyncMock(return_value=channel)
    pending = Pending("random-id", 1, "item/fileChange/requestApproval", {}, "task", "turn")
    assert await transport.send("details", "[dca:random-id:control]", pending) == 9
    kwargs = channel.send.call_args.kwargs
    assert kwargs["allowed_mentions"].everyone is False
    buttons = kwargs["view"].children
    assert [(b.label, b.style) for b in buttons] == [
        ("Approve", discord.ButtonStyle.success),
        ("Deny", discord.ButtonStyle.danger),
    ]
    assert buttons[0].custom_id == "dca:random-id:approve"


async def test_button_acknowledged_before_decision(config, tmp_path):
    store = StateStore(tmp_path / "adapter-state", "identity", "manual")
    client = BridgeClient(config, store)
    order = []

    async def ack(**kwargs):
        order.append("ack")

    def decide(*args, **kwargs):
        order.append("decision")
        return "decided"

    client.engine.decide = decide
    interaction = SimpleNamespace(
        data={"custom_id": "dca:key:approve"},
        response=SimpleNamespace(defer=ack),
        message=SimpleNamespace(author=client.user, id=99),
        user=SimpleNamespace(id=11),
        guild_id=22,
        channel_id=33,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await client.on_interaction(interaction)
    assert order == ["ack", "decision"]
    assert interaction.followup.send.call_args.kwargs["ephemeral"]
    await client.close()


async def test_disconnect_does_not_stop_codex(config, tmp_path):
    client = BridgeClient(config, StateStore(tmp_path / "adapter-state", "identity", "manual"))
    client.engine.stop = AsyncMock()
    await client.on_disconnect()
    client.engine.stop.assert_not_awaited()
    assert not client.engine.connected
    await client.close()


async def test_ready_record_tracks_validated_gateway_and_disconnect(config, tmp_path, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "test-invocation")
    client = BridgeClient(config, StateStore(tmp_path / "adapter-state", "identity", "manual"))
    guild = SimpleNamespace(id=22, me=object())
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = guild
    channel.type = discord.ChannelType.text
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True
    )
    client.get_guild = lambda _: guild
    client.get_channel = lambda _: channel
    path = client.store.directory / "ready.json"
    channel.type = discord.ChannelType.news
    await client.on_ready()
    assert json.loads(path.read_text())["ready"] is False
    channel.type = discord.ChannelType.text
    await client.on_ready()
    assert json.loads(path.read_text())["ready"] is True
    assert json.loads(path.read_text())["invocation"] == "test-invocation"
    assert json.loads(path.read_text())["runtime"] == client.runtime.record()
    await client.on_disconnect()
    assert json.loads(path.read_text())["ready"] is False
    await client.close()


async def test_missing_channel_permissions_prevent_submission(config, tmp_path):
    client = BridgeClient(config, StateStore(tmp_path / "adapter-state", "identity", "manual"))
    client.workspaces.message = AsyncMock()
    guild = SimpleNamespace(id=22, me=object())
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild, channel.id, channel.type = guild, 44, discord.ChannelType.text
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True
    )
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=11),
        guild=guild,
        channel=channel,
        id=999,
        content="edit files",
    )
    await client.on_message(message)
    client.workspaces.message.assert_not_awaited()
    assert client.delivery.queue.qsize() == 1
    await client.close()


async def test_help_is_inline_complete_unicode_without_model_or_mentions(config, tmp_path):
    client = BridgeClient(config, StateStore(tmp_path / "adapter-state", "identity", "manual"))
    guild = SimpleNamespace(id=22, me=object())
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild, channel.id, channel.type = guild, 33, discord.ChannelType.text
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True
    )
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
    client.get_channel = lambda _: channel
    client.wait_until_ready = AsyncMock()
    client.delivery.transport.find = AsyncMock(return_value=None)
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=11),
        guild=guild,
        channel=channel,
        id=999,
        content="!help",
        attachments=[],
        stickers=[],
    )
    client.delivery.start()
    try:
        await client.on_message(message)
        await client.delivery.queue.join()
        await client.on_message(message)  # Duplicate Gateway delivery must not send help twice.
        await client.delivery.queue.join()
        calls = channel.send.await_args_list
        assert len(calls) == 2
        sent = "".join(call.args[0].rpartition("\n")[0] for call in calls)
        assert sent == WORKSPACE_HELP + "\n" + HELP
        assert "\u2014" in sent and "\u00e2\u20ac\u201d" not in sent
        for call in calls:
            assert "file" not in call.kwargs
            assert call.kwargs["allowed_mentions"].everyone is False
            assert len(call.args[0].encode("utf-16-le")) // 2 <= 2000
            # Exercise discord.py's actual JSON serializer, not a legacy file decoder.
            wire = discord.utils._to_json({"content": call.args[0]})
            assert json.loads(wire.encode("utf-8"))["content"] == call.args[0]
        assert client.workspaces.active() is None and client.engine.rpc is None
    finally:
        await client.close()


@pytest.mark.parametrize("channel_id", [33, 44])
@pytest.mark.parametrize("command", ["!ping", "!status"])
async def test_runtime_diagnostics_are_inline_without_model_in_any_channel(
    config, tmp_path, channel_id, command
):
    client = BridgeClient(config, StateStore(tmp_path / "adapter-state", "identity", "manual"))
    guild = SimpleNamespace(id=22, me=object())
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild, channel.id, channel.type = guild, channel_id, discord.ChannelType.text
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True
    )
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
    client.get_channel = lambda _: channel
    client.wait_until_ready = AsyncMock()
    delivery = client.channel_delivery(channel_id)
    delivery.transport.find = AsyncMock(return_value=None)
    delivery.start()
    try:
        await client.on_message(
            SimpleNamespace(
                author=SimpleNamespace(bot=False, id=11),
                guild=guild,
                channel=channel,
                id=999,
                content=command,
                attachments=[],
                stickers=[],
            )
        )
        await delivery.queue.join()
        sent = "\n".join(call.args[0] for call in channel.send.await_args_list)
        assert client.runtime.summary() in sent
        assert "inline-text (no file uploads)" in sent
        assert all(
            "file" not in call.kwargs and "files" not in call.kwargs
            for call in channel.send.await_args_list
        )
        assert client.engine.rpc is None and client.workspaces.active() is None
    finally:
        await client.close()


async def test_saved_legacy_result_is_recovered_inline_after_restart(config, tmp_path):
    store = StateStore(tmp_path / "adapter-state", "identity", "manual")
    content = "Saved output — café 中文 😀 @everyone\n" * 1000
    state = store.load()
    state.last_result, state.delivered, state.result_id = content, True, "legacy-result"
    store.save(state)
    client = BridgeClient(config, store)
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=9)))
    transport = client.delivery.transport
    transport.channel = AsyncMock(return_value=channel)
    transport.find = AsyncMock(return_value=None)
    client.delivery.start()
    try:
        from discord_coding_agent.engine import Origin

        await client.workspaces.message(Origin(11, 22, 33, 999), "!last")
        await client.delivery.queue.join()
        calls = channel.send.await_args_list
        assert len(calls) > 8
        assert "".join(call.args[0].rpartition("\n")[0] for call in calls) == (
            "Session: main\n" + content
        )
        assert all("file" not in call.kwargs and "files" not in call.kwargs for call in calls)
        assert client.engine.rpc is None
    finally:
        await client.close()
