import asyncio
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from conftest import StubRpc, approval, begin, complete

from discord_coding_agent import deployment
from discord_coding_agent.delivery import Delivery
from discord_coding_agent.discord_client import BridgeClient, DiscordTransport
from discord_coding_agent.engine import HELP, Origin, Pending
from discord_coding_agent.errors import BridgeError
from discord_coding_agent.state import StateStore
from discord_coding_agent.workspaces import WORKSPACE_HELP


@pytest.mark.parametrize("status", [429, 500, 503, 403, 404])
async def test_history_http_errors_are_classified_without_response_bodies(status):
    async def history(**kwargs):
        assert kwargs == {"limit": 100}
        raise discord.HTTPException(
            SimpleNamespace(status=status, reason="private response"), "secret server body"
        )
        yield  # Async iterator, matching discord.py's history interface.

    transport = DiscordTransport(SimpleNamespace())
    transport.channel = AsyncMock(return_value=SimpleNamespace(history=history))
    expected = OSError if status == 429 or status >= 500 else BridgeError
    with pytest.raises(expected) as failure:
        await transport.find("marker")
    assert "secret" not in str(failure.value) and "private" not in str(failure.value)
    if expected is BridgeError:
        assert "Read Message History" in str(failure.value)


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


@pytest.fixture
async def connected_client(config, monkeypatch):
    config = replace(config, timeouts=replace(config.timeouts, user_wait=10))
    monkeypatch.setenv("INVOCATION_ID", "resume-invocation")
    client = BridgeClient(config, StateStore(config.state_dir, "identity", "manual"))
    await client._async_setup_hook()  # Bind discord.py dispatch to this test loop; no login/socket.
    guild = SimpleNamespace(id=22, me=object())
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild, channel.type = guild, discord.ChannelType.text
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True
    )
    client.get_guild = lambda _: guild
    client.get_channel = lambda _: channel
    await client.on_ready()
    yield client, guild, channel
    await client.close()


async def test_real_resumed_dispatch_restores_deployment_readiness_without_replaying_work(
    connected_client, owner, monkeypatch, tmp_path
):
    client, _, _ = connected_client
    engine = client.engine
    rpc = StubRpc(engine)

    async def prepare():
        engine.rpc, engine.state.thread_id = rpc, "thread-1"
        engine.verified = True
        engine.save()

    engine._prepare = prepare
    await begin(engine, owner)
    pending = approval(engine)
    worker, turn, calls = engine.worker, engine.turn_id, list(rpc.calls)
    updater = deployment.Updater(
        client.config, deployment.Settings("owner/project", tmp_path / "deploy")
    )
    monkeypatch.setattr(
        deployment,
        "run",
        lambda *a, **k: (
            f"ActiveState=active\nMainPID={os.getpid()}\nInvocationID=resume-invocation"
        ),
    )
    assert updater.healthy()
    await client.on_disconnect()
    assert not updater.healthy() and not engine.connected and engine.busy
    restored = asyncio.Event()
    original = client.readiness

    def readiness(ready):
        original(ready)
        if ready:
            restored.set()

    client.readiness = readiness
    # Exercise the installed library's actual RESUMED parser/event dispatch, not on_ready.
    client._connection.parse_resumed({})
    await asyncio.wait_for(restored.wait(), 1)
    assert updater.healthy() and client.workspaces.connected and engine.connected
    assert engine.worker is worker and engine.turn_id == turn and engine.busy
    assert engine.pending[pending.id] is pending and pending.message_id == 900
    assert rpc.calls == calls and not rpc.closed
    engine.decide(Origin(11, 22, 33, 999), pending.id, True, button_message=900)
    complete(engine, "survived reconnect")
    await worker
    assert engine.state.last_result == "survived reconnect"


@pytest.mark.parametrize("failure", ["guild", "member", "channel", "permissions"])
async def test_resume_revalidates_primary_channel_access(connected_client, failure):
    client, guild, channel = connected_client
    await client.on_disconnect()
    if failure == "guild":
        client.get_guild = lambda _: None
    elif failure == "member":
        guild.me = None
    elif failure == "channel":
        channel.type = discord.ChannelType.news
    else:
        channel.permissions_for.return_value = discord.Permissions(view_channel=True)
    await client.on_resumed()
    assert json.loads((client.store.directory / "ready.json").read_text())["ready"] is False


async def test_resume_recovers_delivery_once_and_does_not_repeat_greeting(connected_client):
    client, _, _ = connected_client
    client.engine.state.last_result = "waiting for Discord — " * 200
    client.engine.state.result_id = "saved-result"
    client.engine.state.delivered = False
    client.engine.save()
    before = client.delivery.queue.qsize()
    for _ in range(2):
        await client.on_disconnect()
        await client.on_resumed()
    assert client.delivery.queue.qsize() == before + 1  # One keyed result, no new greeting.
    assert client.engine.rpc is None


@pytest.mark.parametrize("event", ["on_ready", "on_resumed"])
async def test_late_gateway_events_cannot_restore_readiness_during_shutdown(
    connected_client, event
):
    client, _, _ = connected_client
    client.shutting_down = True
    try:
        client.readiness(False)
        await getattr(client, event)()
        assert json.loads((client.store.directory / "ready.json").read_text())["ready"] is False
    finally:
        client.shutting_down = False


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
        assert 2 <= len(calls) <= 4
        sent = "".join(call.args[0].rpartition("\n")[0] for call in calls)
        assert sent == HELP + "\n\n" + WORKSPACE_HELP
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
            "-# Session: main\n" + content
        )
        assert all("file" not in call.kwargs and "files" not in call.kwargs for call in calls)
        assert client.engine.rpc is None
    finally:
        await client.close()


async def test_subtext_footer_still_reconciles_bot_messages_only():
    client = SimpleNamespace(user=object())
    transport = DiscordTransport(client)
    marker = "[dca:stable-key:inline:1]"
    messages = [
        SimpleNamespace(id=1, author=object(), content="forged\n-# " + marker),
        SimpleNamespace(id=2, author=client.user, content="reply\n-# " + marker),
    ]

    async def history(**kwargs):
        assert kwargs["limit"] == 100
        for message in messages:
            yield message

    transport.channel = AsyncMock(return_value=SimpleNamespace(history=history))
    assert await transport.find(marker) == 2


@pytest.mark.parametrize("command", ["!status", "!status full"])
async def test_status_is_one_grouped_reply_and_keeps_diagnostics(connected_client, owner, command):
    client, _, channel = connected_client
    # Ordinary Discord permissions: no Embed Links or Attach Files are needed.
    channel.send = AsyncMock(return_value=SimpleNamespace(id=901))
    delivery = client.delivery
    delivery.transport.channel = AsyncMock(return_value=channel)
    delivery.transport.find = AsyncMock(return_value=None)
    # Discard startup greeting so this test observes only its requested status.
    while not delivery.queue.empty():
        _, _, job = delivery.queue.get_nowait()
        delivery.keys.discard(job.key)
        delivery.queue.task_done()
    delivery.start()
    await client.workspaces.message(owner, command)
    await delivery.queue.join()
    calls = channel.send.await_args_list
    assert len(calls) == 1
    text = calls[0].args[0]
    assert text.count("Session: main") == 1 and "**State:" in text
    assert "Last observed activity" in text and "Follow-ups:" in text
    assert ("ordinary RPC limit" in text) == (command == "!status full")
    assert "\n-# [dca:" in text
    assert not {"file", "files", "embed", "embeds"} & calls[0].kwargs.keys()
