"""discord.py Gateway adapter. No inbound server or slash command registration."""

import asyncio
import io
import logging
from typing import Any

import discord

from . import protocol
from .config import Config
from .delivery import Delivery
from .engine import Engine, Origin, Pending
from .errors import BridgeError, log_error
from .state import StateStore

log = logging.getLogger(__name__)


class DiscordTransport:
    def __init__(self, client: "BridgeClient") -> None:
        self.client = client

    async def channel(self) -> discord.TextChannel:
        await self.client.wait_until_ready()
        channel = self.client.get_channel(self.client.config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise BridgeError(
                "Configured channel is not an accessible server text channel; check IDs and overrides."
            )
        return channel

    async def send(
        self, text: str, data: bytes | None, marker: str, pending: Pending | None
    ) -> int:
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
        if data is not None:
            kwargs["file"] = discord.File(io.BytesIO(data), filename="codex-details.txt")
        try:
            message = await channel.send(f"{text}\n{marker}", **kwargs)
            return message.id
        except discord.HTTPException as exc:
            if exc.status >= 500 or exc.status == 429:
                raise OSError("Transient Discord delivery failure") from None
            raise BridgeError(
                f"Discord send failed (HTTP {exc.status}); check Send Messages and Attach Files permissions."
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
            self.client.forget_control(message_id)
        except discord.NotFound:
            self.client.forget_control(message_id)

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
        self.transport = DiscordTransport(self)
        self.delivery = Delivery(
            self.transport,
            config.timeouts.delivery,
            lambda key: key in self.engine.pending,
            lambda key, message: self.engine.bind(key, message),
            self.delivered,
        )
        self.engine = Engine(config, store, self.delivery, connection_only=connection_only)
        self.cosmetic_worker: asyncio.Task[None] | None = None
        self.ready_once = False
        self.shutting_down = False

    async def setup_hook(self) -> None:
        self.delivery.start()
        self.cosmetic_worker = asyncio.create_task(self.cosmetics())

    def delivered(self, result_id: str) -> None:
        if result_id == self.engine.state.result_id:
            self.engine.state.delivered = True
            self.engine.save()

    def forget_control(self, message_id: int) -> None:
        if message_id in self.engine.state.controls:
            self.engine.state.controls.remove(message_id)
            self.engine.save()

    async def on_ready(self) -> None:
        self.engine.connected = True
        guild = self.get_guild(self.config.guild_id)
        channel = self.get_channel(self.config.channel_id)
        if (
            not guild
            or not isinstance(channel, discord.TextChannel)
            or channel.guild.id != guild.id
        ):
            log.error(
                "gateway=configuration_error; configured guild/channel inaccessible; check IDs and View Channels"
            )
            return
        assert guild.me
        permissions = channel.permissions_for(guild.me)
        missing = [
            name
            for name in ["view_channel", "send_messages", "read_message_history", "attach_files"]
            if not getattr(permissions, name)
        ]
        if missing:
            log.error("gateway=permissions_missing permissions=%s", ",".join(missing))
            return
        log.info("gateway=ready user_id=%s", self.user.id if self.user else None)
        active_controls = {pending.message_id for pending in self.engine.pending.values()}
        for message_id in list(self.engine.state.controls):
            if message_id not in active_controls:
                self.delivery.disable(message_id)
        if not self.ready_once:
            self.ready_once = True
            self.delivery.text(
                f"{self.config.display_name} connected. Use !ping, then !help. "
                + (
                    "Previous work was interrupted; review git status/diff before sending a continuation. It was not replayed."
                    if self.engine.state.interrupted
                    else ""
                )
            )
        if self.engine.state.last_result and not self.engine.state.delivered:
            self.delivery.text(self.engine.state.last_result, result_id=self.engine.state.result_id)
        # Existing pending requests remain scoped to the same process and turn on reconnect.
        for pending in list(self.engine.pending.values()):
            if pending.message_id is None:
                self.delivery.request(pending)

    async def on_disconnect(self) -> None:
        self.engine.connected = False
        log.warning("gateway=disconnected; coding task is independent")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        origin = Origin(
            message.author.id,
            message.guild.id if message.guild else None,
            message.channel.id,
            message.id,
        )
        try:
            await self.engine.message(
                origin, message.content, bool(message.attachments or message.stickers)
            )
        except Exception as exc:
            log_error(log, "discord-message", exc)
            if self.engine.authorized(origin):
                self.delivery.text(
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
                answer = self.engine.decide(
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
            if self.engine.busy and self.is_ready():
                await self.delivery.cosmetic()

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        # discord.py's default error handler may log event arguments. Keep them private.
        log.error("discord_event_error=%s; inspect status and doctor", event_method)

    async def close(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        try:
            await self.engine.close()
        finally:
            if self.cosmetic_worker:
                self.cosmetic_worker.cancel()
                await asyncio.gather(self.cosmetic_worker, return_exceptions=True)
            await self.delivery.close()
            await super().close()
