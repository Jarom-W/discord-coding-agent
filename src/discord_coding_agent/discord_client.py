"""discord.py Gateway adapter. No inbound server or slash command registration."""

import asyncio
import json
import logging
import os
from typing import Any

import discord

from . import protocol, runtime
from .config import Config
from .delivery import Delivery
from .engine import Engine, Origin, Pending
from .errors import BridgeError, log_error
from .state import StateStore, atomic_write
from .workspaces import Workspaces

log = logging.getLogger(__name__)


class DiscordTransport:
    def __init__(self, client: "BridgeClient", channel_id: int | None = None) -> None:
        self.client = client
        self.channel_id = channel_id

    async def channel(self) -> discord.TextChannel:
        await self.client.wait_until_ready()
        channel = self.client.get_channel(self.channel_id or self.client.config.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self.client.config.guild_id
            or channel.type != discord.ChannelType.text
        ):
            raise BridgeError(
                "Configured channel is not an accessible server text channel; check IDs and overrides."
            )
        return channel

    async def send(self, text: str, marker: str, pending: Pending | None) -> int:
        channel = await self.channel()
        view: discord.ui.View | None = None
        # Only the final control message gets buttons, after all details are delivered.
        if pending and marker.endswith(":control]") and pending.method in protocol.APPROVALS:
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    label="Approve",
                    style=discord.ButtonStyle.success,
                    custom_id=f"dca:{pending.id}:approve",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label="Deny",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"dca:{pending.id}:deny",
                )
            )
        kwargs: dict[str, Any] = {"allowed_mentions": discord.AllowedMentions.none(), "view": view}
        try:
            message = await channel.send(f"{text}\n-# {marker}", **kwargs)
            return message.id
        except discord.HTTPException as exc:
            if exc.status >= 500 or exc.status == 429:
                raise OSError("Transient Discord delivery failure") from None
            raise BridgeError(
                f"Discord send failed (HTTP {exc.status}); check Send Messages permissions."
            ) from None
        finally:
            # Global on_interaction handles stale controls too; don't retain view callbacks.
            if view:
                view.stop()

    async def find(self, marker: str) -> int | None:
        channel = await self.channel()
        async for message in channel.history(limit=100):
            if message.author == self.client.user and message.content.endswith(marker):
                return message.id
        return None

    async def disable(self, message_id: int) -> None:
        try:
            channel = await self.channel()
            message = await channel.fetch_message(message_id)
            if message.author == self.client.user:
                await message.edit(view=None)
            self.client.forget_control(message_id, self.channel_id)
        except discord.NotFound:
            self.client.forget_control(message_id, self.channel_id)

    async def typing(self) -> None:
        channel = await self.channel()
        await channel.typing()


class BridgeClient(discord.Client):
    def __init__(self, config: Config, store: StateStore, connection_only: bool = False) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(
            intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=128
        )
        self.config = config
        self.runtime = runtime.current()
        self.store = store
        self.deliveries: dict[int, Delivery] = {}
        self.delivery_started = False
        self.workspaces = Workspaces(
            config, store, self.channel_delivery, connection_only=connection_only
        )
        self.workspaces.engine(self.workspaces.sessions["default"])
        self.cosmetic_worker: asyncio.Task[None] | None = None
        self.ready_once = False
        self.shutting_down = False
        self.readiness(False)

    @property
    def engine(self) -> Engine:
        """Configured channel's selection (legacy embedding API)."""
        selected = self.workspaces.selected(self.config.channel_id)
        return self.workspaces.engine(selected or self.workspaces.sessions["default"])

    @property
    def delivery(self) -> Delivery:
        return self.channel_delivery(self.config.channel_id)

    def readiness(self, ready: bool) -> None:
        atomic_write(
            self.store.directory / "ready.json",
            json.dumps(
                {
                    "protocol": 1,
                    "pid": os.getpid(),
                    "invocation": os.environ.get("INVOCATION_ID", ""),
                    "config": str(self.config.path),
                    "ready": ready,
                    "runtime": self.runtime.record(),
                }
            )
            + "\n",
        )

    def channel_delivery(self, channel: int) -> Delivery:
        if channel not in self.deliveries:

            def valid(key: str) -> bool:
                return any(key in e.pending for e in self.workspaces.channel_engines(channel))

            def bind(key: str, message: int) -> bool:
                engine = next(
                    (e for e in self.workspaces.channel_engines(channel) if key in e.pending), None
                )
                return bool(engine and engine.bind(key, message))

            delivery = Delivery(
                DiscordTransport(self, channel),
                self.config.timeouts.delivery,
                valid,
                bind,
                self.delivered,
            )
            self.deliveries[channel] = delivery
            if self.delivery_started:
                delivery.start()
        return self.deliveries[channel]

    async def setup_hook(self) -> None:
        self.delivery_started = True
        for delivery in self.deliveries.values():
            delivery.start()
        self.cosmetic_worker = asyncio.create_task(self.cosmetics())

    def delivered(self, result_id: str) -> None:
        for engine in self.workspaces.engines.values():
            if result_id == engine.state.result_id:
                engine.state.delivered = True
                engine.save()

    def forget_control(self, message_id: int, channel: int | None = None) -> None:
        for engine in self.workspaces.channel_engines(channel or self.config.channel_id):
            if message_id in engine.state.controls:
                engine.state.controls.remove(message_id)
                engine.save()

    async def on_ready(self) -> None:
        self.gateway_ready("ready")

    async def on_resumed(self) -> None:
        # A successful reconnect emits RESUMED instead of another READY. Both paths
        # must restore the bridge's own connection flags and deployment readiness.
        self.gateway_ready("resumed")

    def gateway_ready(self, event: str) -> None:
        if self.shutting_down:
            return
        self.readiness(False)
        self.workspaces.connected = True
        for engine in self.workspaces.engines.values():
            engine.connected = True
        guild = self.get_guild(self.config.guild_id)
        channel = self.get_channel(self.config.channel_id)
        if (
            not guild
            or not guild.me
            or not isinstance(channel, discord.TextChannel)
            or channel.guild.id != guild.id
            or channel.type != discord.ChannelType.text
        ):
            log.error(
                "gateway=configuration_error; configured guild/channel inaccessible; check IDs and View Channels"
            )
            return
        permissions = channel.permissions_for(guild.me)
        missing = [
            name
            for name in ["view_channel", "send_messages", "read_message_history"]
            if not getattr(permissions, name)
        ]
        if missing:
            log.error("gateway=permissions_missing permissions=%s", ",".join(missing))
            return
        for selected_id in self.workspaces.channels.values():
            if selected_id:
                self.workspaces.engine(self.workspaces.sessions[selected_id])
        for engine in self.workspaces.engines.values():
            delivery = self.channel_delivery(engine.config.channel_id)
            active_controls = {p.message_id for p in engine.pending.values()}
            for message_id in list(engine.state.controls):
                if message_id not in active_controls:
                    delivery.disable(message_id)
            if engine.state.last_result and not engine.state.delivered:
                delivery.text(engine.state.last_result, result_id=engine.state.result_id)
            for pending in list(engine.pending.values()):
                if pending.message_id is None:
                    delivery.request(pending)
        if not self.ready_once:
            self.ready_once = True
            self.delivery.text(
                f"{self.config.display_name} connected. {self.runtime.summary()} Use !ping, then !help. "
                + (
                    "Previous work was interrupted; review git status/diff before sending a continuation. It was not replayed."
                    if self.engine.state.interrupted
                    else ""
                )
            )

        self.readiness(True)
        log.info(
            "gateway=ready source_event=%s user_id=%s bridge=%s revision=%s replies=%s",
            event,
            self.user.id if self.user else None,
            self.runtime.version,
            self.runtime.revision or "unknown",
            self.runtime.replies,
        )

    async def on_disconnect(self) -> None:
        self.workspaces.connected = False
        for engine in self.workspaces.engines.values():
            engine.connected = False
        self.readiness(False)
        log.warning("gateway=disconnected; coding task is independent")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id != self.config.owner_id:
            return
        if message.guild is None or message.guild.id != self.config.guild_id:
            return
        if (
            not isinstance(message.channel, discord.TextChannel)
            or message.channel.type != discord.ChannelType.text
        ):
            return
        if not message.guild.me:
            return
        permissions = message.channel.permissions_for(message.guild.me)
        missing = [
            name
            for name in ["view_channel", "send_messages", "read_message_history"]
            if not getattr(permissions, name)
        ]
        if missing:
            log.warning("channel=%s permissions_missing=%s", message.channel.id, ",".join(missing))
            self.delivery.text(
                f"Channel {message.channel.id} is missing bot permissions: {', '.join(missing)}. Nothing was submitted. Check channel/category overrides."
            )
            return
        origin = Origin(
            message.author.id,
            message.guild.id if message.guild else None,
            message.channel.id,
            message.id,
        )
        try:
            await self.workspaces.message(
                origin, message.content, bool(message.attachments or message.stickers)
            )
        except Exception as exc:
            log_error(log, "discord-message", exc)
            if self.workspaces.permitted(origin):
                self.channel_delivery(
                    origin.channel
                    if origin.channel in self.workspaces.channels
                    else self.config.channel_id
                ).text(
                    "Bridge could not safely process this message. Inspect the journal and !status before retrying."
                )

    async def on_interaction(self, interaction: discord.Interaction[Any]) -> None:
        data: Any = interaction.data or {}
        custom_id = data.get("custom_id", "")
        if not isinstance(custom_id, str) or not custom_id.startswith("dca:"):
            return
        try:
            # Acknowledge before disk writes or decision processing (Discord has a short deadline).
            await asyncio.wait_for(interaction.response.defer(ephemeral=True, thinking=True), 2)
            parts = custom_id.split(":")
            message = interaction.message
            if (
                len(parts) != 3
                or parts[2] not in {"approve", "deny"}
                or not message
                or message.author != self.user
            ):
                raise BridgeError("Invalid or expired control message.")
            origin = Origin(
                interaction.user.id, interaction.guild_id, interaction.channel_id or 0, message.id
            )
            try:
                selected = self.workspaces.selected(origin.channel)
                if not selected or not self.workspaces.permitted(origin):
                    raise BridgeError("Invalid or expired control message for this channel.")
                answer = self.workspaces.engine(selected).decide(
                    origin, parts[1], parts[2] == "approve", button_message=message.id
                )
            except BridgeError as exc:
                answer = str(exc)
            await asyncio.wait_for(
                interaction.followup.send(
                    answer, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                ),
                self.config.timeouts.delivery,
            )
        except Exception as exc:
            log_error(log, "discord-interaction", exc)

    async def cosmetics(self) -> None:
        while True:
            await asyncio.sleep(8)
            active = self.workspaces.active()
            if active and self.is_ready():
                await self.channel_delivery(active.config.channel_id).cosmetic()

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        # discord.py's default error handler may log event arguments. Keep them private.
        log.error("discord_event_error=%s; inspect status and doctor", event_method)

    async def close(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        try:
            self.readiness(False)
            await self.workspaces.close()
        finally:
            if self.cosmetic_worker:
                self.cosmetic_worker.cancel()
                await asyncio.gather(self.cosmetic_worker, return_exceptions=True)
            await asyncio.gather(*(delivery.close() for delivery in self.deliveries.values()))
            await super().close()
