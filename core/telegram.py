"""
Telegram notifier — sends alerts and status updates via the Bot API.
Uses aiogram Bot for async HTTP calls.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

if TYPE_CHECKING:
    from core.node_manager import Node


class TelegramNotifier:
    """Sends messages to a Telegram chat via the Bot API."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        config_dir: Optional[Path] = None,
    ) -> None:
        # Priority: explicit args > env vars > global.yaml
        if bot_token and chat_id:
            self.bot_token = bot_token
            self.chat_id = chat_id
        else:
            self.bot_token, self.chat_id = self._resolve_credentials(config_dir)

        self.enabled = bool(self.bot_token and self.chat_id)
        self._bot: Optional[Bot] = None
        if self.enabled:
            self._bot = Bot(
                token=self.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )

    # ------------------------------------------------------------------
    # Credential resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_credentials(
        config_dir: Optional[Path],
    ) -> tuple[str, str]:
        """Try env vars first, then global.yaml."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if token and chat_id:
            return token, chat_id

        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"

        global_path = config_dir / "global.yaml"
        if global_path.exists():
            with open(global_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            tg = cfg.get("telegram", {})
            token = token or str(tg.get("bot_token", ""))
            chat_id = chat_id or str(tg.get("chat_id", ""))

        return token, chat_id

    # ------------------------------------------------------------------
    # Core send method
    # ------------------------------------------------------------------

    async def send_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> Optional[int]:
        """
        Send a text message to the configured chat.
        Returns message_id on success, None on failure.
        """
        if not self.enabled or not self._bot:
            return None

        try:
            msg = await self._bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return msg.message_id
        except Exception as exc:
            print(f"[telegram] Send error: {exc}")
            return None

    async def delete_message(self, message_id: int) -> bool:
        """Delete a previously sent message by message_id."""
        if not self.enabled or not self._bot:
            return False
        try:
            await self._bot.delete_message(
                chat_id=self.chat_id, message_id=message_id,
            )
            return True
        except Exception as exc:
            print(f"[telegram] delete_message {message_id}: {exc}")
            return False

    async def close(self) -> None:
        if self._bot:
            await self._bot.session.close()

    # ------------------------------------------------------------------
    # Structured notifications
    # ------------------------------------------------------------------

    async def notify_node_down(
        self, node: "Node", reason: str = "Health check failed"
    ) -> Optional[int]:
        """Returns message_id so caller can delete it on recovery."""
        text = (
            f"🔴 <b>NODE DOWN</b>\n"
            f"Node: <code>{node.name}</code> ({node.ip})\n"
            f"Region: {node.region.upper()}\n"
            f"Reason: {reason}"
        )
        return await self.send_message(text)

    async def notify_node_recovered(self, node: "Node") -> Optional[int]:
        """Used only after a full failover cycle (failback)."""
        text = (
            f"🟢 <b>NODE RECOVERED</b>\n"
            f"Node: <code>{node.name}</code> ({node.ip})\n"
            f"Region: {node.region.upper()}\n"
            f"Restored to bridge rotation."
        )
        return await self.send_message(text)

    async def notify_failover(
        self, from_node: "Node", to_node: "Node"
    ) -> Optional[int]:
        text = (
            f"⚠️ <b>FAILOVER TRIGGERED</b>\n"
            f"Failed node: <code>{from_node.name}</code> ({from_node.ip})\n"
            f"Backup node: <code>{to_node.name}</code> ({to_node.ip})\n"
            f"Bridge routing has been updated."
        )
        return await self.send_message(text)

    async def notify_deploy_complete(self, node: "Node") -> Optional[int]:
        text = (
            f"✅ <b>DEPLOYMENT COMPLETE</b>\n"
            f"Node: <code>{node.name}</code> ({node.ip})\n"
            f"Region: {node.region.upper()}\n"
            f"Protocols: {', '.join(node.protocols) or 'n/a'}"
        )
        return await self.send_message(text)

    async def notify_deploy_failed(self, node: "Node", error: str) -> Optional[int]:
        text = (
            f"❌ <b>DEPLOYMENT FAILED</b>\n"
            f"Node: <code>{node.name}</code> ({node.ip})\n"
            f"Error: {error}"
        )
        return await self.send_message(text)
