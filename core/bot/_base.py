"""BotBase — shared state, init and helper methods for all handler mixins."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import TYPE_CHECKING, Optional

import aiohttp
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, Message

from core.bot._helpers import AuthMiddleware, _PROJECT_ROOT, _kb

if TYPE_CHECKING:
    from core.bridge_manager import BridgeManager
    from core.deployer import NodeDeployer
    from core.monitor import NodeMonitor
    from core.node_manager import Node, NodeManager


class BotBase:
    """Shared state and helpers inherited by all handler mixins."""

    _SERVICES: dict[str, str] = {
        "xray":  "xray",
        "hy2":   "hysteria2",
        "awg":   "wg-quick@awg0",
    }

    _kb = staticmethod(_kb)

    def __init__(
        self,
        token: str,
        chat_id: str,
        allowed_user_ids: list[int],
        nm: "NodeManager",
        global_cfg: dict,
        monitor: Optional["NodeMonitor"] = None,
        bm: Optional["BridgeManager"] = None,
        deployer: Optional["NodeDeployer"] = None,
    ) -> None:
        # --- aiogram ---
        self.bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher(storage=MemoryStorage())
        self.router = Router()

        self.chat_id = chat_id
        self.allowed_user_ids: set[int] = set(allowed_user_ids)
        self.nm = nm
        self.cfg = global_cfg
        self.monitor = monitor
        self.bm = bm
        self.deployer = deployer

        # --- runtime state ---
        self._awg_peer_locks: dict[str, threading.Lock] = {}
        self._awg_peer_locks_guard = threading.Lock()
        self._portal_was_down: bool = False
        self._github_cache: dict[str, tuple[str, float]] = {}
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._disabled_nodes: set[str] = set()
        self._node_overrides_path = str(
            _PROJECT_ROOT / "config" / "node_overrides.json"
        )
        self._load_node_overrides()

        # --- aiogram wiring (calls subclass _register_handlers) ---
        auth = AuthMiddleware(self.allowed_user_ids)
        self.router.message.middleware(auth)
        self.router.callback_query.middleware(auth)
        self._register_handlers()  # implemented in BulwarkBot
        self.dp.include_router(self.router)

    # ------------------------------------------------------------------
    # Messaging helpers
    # ------------------------------------------------------------------

    async def _send(
        self, chat_id: int | str, text: str,
        markup: Optional[InlineKeyboardMarkup] = None,
    ) -> Optional[int]:
        try:
            msg = await self.bot.send_message(
                chat_id, text[:4096], reply_markup=markup,
            )
            return msg.message_id
        except Exception as e:
            print(f"[bot] sendMessage: {e}")
            return None

    async def _edit(
        self, chat_id: int | str, msg_id: int, text: str,
        markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        try:
            await self.bot.edit_message_text(
                text[:4096], chat_id=chat_id, message_id=msg_id,
                reply_markup=markup,
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"[bot] editMessage: {e}")

    async def _delete_msg(self, chat_id: int | str, msg_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id, msg_id)
        except Exception:
            pass

    async def _show(
        self, chat_id: int | str, msg_id: Optional[int],
        text: str, markup: Optional[InlineKeyboardMarkup] = None,
    ) -> Optional[int]:
        """Send new message (msg_id=None) or edit existing. Returns msg_id."""
        if msg_id:
            await self._edit(chat_id, msg_id, text, markup)
            return msg_id
        return await self._send(chat_id, text, markup)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------

    async def _resolve_node(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> Optional["Node"]:
        try:
            return self.nm.get_node(node_name)
        except KeyError:
            await self._edit(chat_id, msg_id, "❌ Нода не найдена.")
            return None

    async def _run(self, fn, *args):
        """Run blocking function in thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def _ssh(
        self, node: "Node", cmd: str, timeout: int = 30,
    ) -> tuple[str, str, int]:
        """Run SSH command via executor. Returns (stdout, stderr, rc)."""
        return await self._run(
            lambda: self.nm.exec_command(node, cmd, timeout=timeout),
        )

    async def _fsm_ctx(
        self, message: Message, state: FSMContext,
    ) -> tuple[dict, int, int]:
        """FSM preamble: delete user msg, return (data, msg_id, chat_id)."""
        try:
            await message.delete()
        except Exception:
            pass
        data = await state.get_data()
        return data, data.get("msg_id"), message.chat.id

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    # ------------------------------------------------------------------
    # Node overrides (enable / disable)
    # ------------------------------------------------------------------

    def _load_node_overrides(self) -> None:
        try:
            with open(self._node_overrides_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._disabled_nodes = set(data.get("disabled", []))
        except FileNotFoundError:
            self._disabled_nodes = set()
        except Exception as e:
            print(f"[bot] node_overrides load: {e}")
            self._disabled_nodes = set()

    def _save_node_overrides(self) -> None:
        try:
            with open(self._node_overrides_path, "w", encoding="utf-8") as f:
                json.dump({"disabled": sorted(self._disabled_nodes)}, f, indent=2)
        except Exception as e:
            print(f"[bot] node_overrides save: {e}")

    def _is_node_disabled(self, node_name: str) -> bool:
        return node_name in self._disabled_nodes

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _get_peer_lock(self, node_name: str):
        """Per-node lock for AWG peer creation (thread-safe)."""
        with self._awg_peer_locks_guard:
            if node_name not in self._awg_peer_locks:
                self._awg_peer_locks[node_name] = threading.Lock()
            return self._awg_peer_locks[node_name]

    def _audit(self, action: str, details: str = "") -> None:
        msg = f"[audit] {action}"
        if details:
            msg += f" — {details}"
        print(msg)
