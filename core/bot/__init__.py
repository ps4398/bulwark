"""
Bulwark Telegram Bot — aiogram 3, inline keyboards.

Entry point: ``from core.bot import BulwarkBot``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, Message

from core.bot._awg import AWGMixin
from core.bot._base import BotBase
from core.bot._helpers import NodeAddStates
from core.bot._loops import LoopsMixin
from core.bot._mgmt import MgmtMixin
from core.bot._nodes import NodesMixin
from core.bot._status import StatusMixin
from core.bot._subs import SubsMixin
from core.bot._traffic import TrafficMixin

try:
    from aiogram.filters import F
except ImportError:  # pragma: no cover
    from aiogram import F  # type: ignore[attr-defined]


class BulwarkBot(
    StatusMixin,
    TrafficMixin,
    SubsMixin,
    MgmtMixin,
    NodesMixin,
    AWGMixin,
    LoopsMixin,
    BotBase,
):
    """Assembles all handler mixins into the final bot class."""

    # ------------------------------------------------------------------
    # Handler registration (callback routing table)
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        r = self.router

        # --- Commands (priority over FSM states) ---
        r.message.register(self._cmd_start, Command("start", "menu", "help"))
        r.message.register(self._cmd_status, Command("status"))
        r.message.register(self._cmd_traffic, Command("traffic"))
        r.message.register(self._cmd_sub, Command("sub"))
        r.message.register(self._cmd_mgmt, Command("mgmt"))
        r.message.register(self._cmd_cancel, Command("cancel"))

        # --- FSM wizard text handlers ---
        r.message.register(self._fsm_node_name, NodeAddStates.waiting_name)
        r.message.register(self._fsm_node_ip, NodeAddStates.waiting_ip)
        r.message.register(self._fsm_node_region, NodeAddStates.waiting_region)
        r.message.register(self._fsm_node_ssh_password, NodeAddStates.waiting_ssh_password)
        r.message.register(self._fsm_node_ssh_key, NodeAddStates.waiting_ssh_key_path)
        r.message.register(self._fsm_node_sni, NodeAddStates.waiting_sni)

        # --- Callback handler factories ---
        def h0(m):
            async def _h(cb: CallbackQuery):
                asyncio.ensure_future(cb.answer())
                await m(cb.message.chat.id, cb.message.message_id)
            return _h

        def h1(m):
            async def _h(cb: CallbackQuery):
                asyncio.ensure_future(cb.answer())
                await m(cb.message.chat.id, cb.message.message_id, cb.data.split(":", 1)[1])
            return _h

        def h2(m):
            async def _h(cb: CallbackQuery):
                asyncio.ensure_future(cb.answer())
                p = cb.data.split(":")
                await m(cb.message.chat.id, cb.message.message_id, p[1], p[2])
            return _h

        def h3(m):
            async def _h(cb: CallbackQuery):
                asyncio.ensure_future(cb.answer())
                p = cb.data.split(":")
                await m(cb.message.chat.id, cb.message.message_id, p[1], p[2], p[3])
            return _h

        def hf0(m):
            async def _h(cb: CallbackQuery, state: FSMContext):
                asyncio.ensure_future(cb.answer())
                await m(cb.message.chat.id, cb.message.message_id, state)
            return _h

        def hf1(m):
            async def _h(cb: CallbackQuery, state: FSMContext):
                asyncio.ensure_future(cb.answer())
                await m(cb.message.chat.id, cb.message.message_id, cb.data.split(":", 1)[1], state)
            return _h

        # --- Callback routing table ---
        # Navigation
        r.callback_query.register(h0(self._main_menu),           F.data == "menu")
        r.callback_query.register(h0(self._status_all),           F.data == "status")
        r.callback_query.register(h1(self._status_node),          F.data.startswith("status_node:"))
        r.callback_query.register(h2(self._do_restart),           F.data.startswith("restart:"))
        r.callback_query.register(h1(self._show_logs),            F.data.startswith("logs:"))
        r.callback_query.register(h0(self._traffic_all),          F.data == "traffic")
        r.callback_query.register(h0(self._traffic_month),        F.data == "traffic_month")
        # Subscriptions
        r.callback_query.register(h0(self._sub_nodes),            F.data == "sub")
        r.callback_query.register(h1(self._sub_protos),           F.data.startswith("sub_node:"))
        r.callback_query.register(h2(self._sub_uri_show),         F.data.startswith("sub_uri:"))
        r.callback_query.register(h0(self._sub_full),             F.data == "sub_full")
        # AmneziaVPN (xray) vpn:// links
        r.callback_query.register(h0(self._amnezia_xray_nodes),  F.data == "ax_nodes")
        r.callback_query.register(h1(self._amnezia_xray_routes), F.data.startswith("ax_node:"))
        r.callback_query.register(h2(self._amnezia_xray_link),   F.data.startswith("ax_link:"))
        # Management
        r.callback_query.register(h0(self._mgmt_menu),            F.data == "mgmt")
        r.callback_query.register(h0(self._bridge_push),          F.data == "bridge_push")
        r.callback_query.register(h0(self._do_sub_push),          F.data == "sub_push")
        # System
        r.callback_query.register(h1(self._show_sysinfo),         F.data.startswith("sysinfo:"))
        r.callback_query.register(h1(self._show_svc_status),      F.data.startswith("svc_status:"))
        r.callback_query.register(h1(self._reboot_confirm),       F.data.startswith("reboot_confirm:"))
        r.callback_query.register(h1(self._reboot_exec),          F.data.startswith("reboot_exec:"))
        # Failover
        r.callback_query.register(h0(self._failover_menu),        F.data == "failover")
        r.callback_query.register(h1(self._failover_select),      F.data.startswith("failover_src:"))
        r.callback_query.register(h2(self._failover_confirm),     F.data.startswith("failover_cfg:"))
        r.callback_query.register(h2(self._failover_exec),        F.data.startswith("failover_exec:"))
        # Portal
        r.callback_query.register(h0(self._portal_check),         F.data == "portal_check")
        r.callback_query.register(h0(self._portal_reload),        F.data == "portal_reload")
        # Upgrades
        r.callback_query.register(h0(self._upgrades_menu),        F.data == "upgrades")
        r.callback_query.register(h1(self._upgrade_node_screen),  F.data.startswith("upgrade_node:"))
        r.callback_query.register(h2(self._do_upgrade_binary),    F.data.startswith("upgrade_exec:"))
        # Cleanup / speedtest
        r.callback_query.register(h1(self._cleanup_logs_confirm), F.data.startswith("cleanup_logs:"))
        r.callback_query.register(h1(self._cleanup_logs_exec),    F.data.startswith("cleanup_exec:"))
        r.callback_query.register(h1(self._speedtest_node),       F.data.startswith("speedtest:"))
        # Node stats & management
        r.callback_query.register(h1(self._node_stats),           F.data.startswith("node_stats:"))
        r.callback_query.register(h0(self._node_mgmt),            F.data == "node_mgmt")
        r.callback_query.register(h1(self._node_toggle_confirm),  F.data.startswith("node_toggle:"))
        r.callback_query.register(h1(self._node_toggle_exec),     F.data.startswith("node_toggle_ok:"))
        # Node wizard (FSM)
        r.callback_query.register(h0(self._node_add_start),       F.data == "node_add_start")
        r.callback_query.register(hf1(self._node_add_type),       F.data.startswith("node_add_type:"))
        r.callback_query.register(hf1(self._node_add_priority),   F.data.startswith("node_add_priority:"))
        r.callback_query.register(hf1(self._node_add_ssh_method), F.data.startswith("node_add_ssh:"))
        r.callback_query.register(hf1(self._node_add_bridge_mode), F.data.startswith("node_add_bmode:"))
        r.callback_query.register(hf1(self._node_add_toggle_protocol), F.data.startswith("node_add_ptoggle:"))
        r.callback_query.register(hf0(self._node_add_summary),    F.data == "node_add_pdone")
        r.callback_query.register(hf0(self._node_add_confirm),    F.data == "node_add_confirm")

        async def _cancel_node_add(cb: CallbackQuery, state: FSMContext):
            asyncio.ensure_future(cb.answer())
            await state.clear()
            await self._node_mgmt(cb.message.chat.id, cb.message.message_id)
        r.callback_query.register(_cancel_node_add, F.data == "node_add_cancel")

        # Node remove
        r.callback_query.register(h0(self._node_rm_start),        F.data == "node_rm_start")
        r.callback_query.register(h1(self._node_rm_select),       F.data.startswith("node_rm_sel:"))
        r.callback_query.register(h1(self._node_rm_confirm),      F.data.startswith("node_rm_ok:"))
        # AWG
        r.callback_query.register(h0(self._awg_nodes),            F.data == "awg_nodes")
        r.callback_query.register(h1(self._awg_peers),            F.data.startswith("awg_peers:"))
        r.callback_query.register(h2(self._awg_peer_show),        F.data.startswith("awg_peer:"))
        r.callback_query.register(h1(self._awg_add_peer_confirm), F.data.startswith("awg_add:"))
        r.callback_query.register(h0(self._awg_sub_link),         F.data == "awg_sub_link")
        r.callback_query.register(h1(self._awg_add_peer_exec),    F.data.startswith("awg_add_exec:"))
        r.callback_query.register(h3(self._awg_link_show),        F.data.startswith("awg_link:"))

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    async def _main_menu(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        text = "<b>🏰 Bulwark</b>\n\nВыбери действие:"
        markup = self._kb([
            [("📊 Статус нод", "status"), ("📈 Трафик", "traffic")],
            [("🔗 VLESS / HY2", "sub"), ("🛡 AmneziaWG", "awg_nodes")],
            [("⚙️ Управление", "mgmt")],
        ])
        await self._show(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self._main_menu(message.chat.id)

    async def _cmd_status(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self._status_all(message.chat.id)

    async def _cmd_traffic(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self._traffic_all(message.chat.id)

    async def _cmd_sub(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self._sub_nodes(message.chat.id)

    async def _cmd_mgmt(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self._mgmt_menu(message.chat.id)

    async def _cmd_cancel(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("❌ Операция отменена.")

    # ------------------------------------------------------------------
    # Bot menu registration
    # ------------------------------------------------------------------

    async def _set_commands(self) -> None:
        commands = [
            BotCommand(command="start",   description="Главное меню"),
            BotCommand(command="status",  description="Статус всех нод"),
            BotCommand(command="traffic", description="Трафик по нодам"),
            BotCommand(command="sub",     description="Подписки и URI"),
            BotCommand(command="mgmt",    description="Управление"),
            BotCommand(command="cancel",  description="Отмена текущей операции"),
        ]
        try:
            await self.bot.set_my_commands(commands)
            print("[bot] Меню команд зарегистрировано.")
        except Exception as e:
            print(f"[bot] setMyCommands: {e}")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self, with_monitor: bool = True) -> None:
        from concurrent.futures import ThreadPoolExecutor
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=20))

        await self._set_commands()
        print("[bot] Запуск aiogram polling...")

        bg_tasks: list[asyncio.Task] = []
        if with_monitor and self.monitor:
            bg_tasks.append(asyncio.create_task(self.monitor.monitor_loop()))
        bg_tasks.append(asyncio.create_task(self._digest_loop()))
        bg_tasks.append(asyncio.create_task(self._traffic_alert_loop()))
        bg_tasks.append(asyncio.create_task(self._portal_monitor_loop()))

        try:
            await self.dp.start_polling(self.bot)
        finally:
            for t in bg_tasks:
                t.cancel()
            if self._http_session and not self._http_session.closed:
                await self._http_session.close()
            await self.bot.session.close()


__all__ = ["BulwarkBot"]
