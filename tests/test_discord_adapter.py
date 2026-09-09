import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from discord_coding_agent.discord_client import BridgeClient, DiscordTransport
from discord_coding_agent.engine import Pending
from discord_coding_agent.state import StateStore


async def test_buttons_styles_ids_and_no_mentions():
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=9)))
    transport = DiscordTransport(SimpleNamespace())
    transport.channel = AsyncMock(return_value=channel)
    pending = Pending("random-id", 1, "item/fileChange/requestApproval", {}, "task", "turn")
    assert await transport.send("details", None, "[dca:random-id:control]", pending) == 9
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
        view_channel=True, send_messages=True, read_message_history=True, attach_files=True
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
        view_channel=True, send_messages=True, read_message_history=True
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
