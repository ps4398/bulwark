"""VLESS / HY2 subscription display handlers."""

from __future__ import annotations

import os
import urllib.parse
from typing import Optional

from core.bot._helpers import _flag, _kb


class SubsMixin:
    """Subscription node/protocol selection and URI display."""

    async def _sub_nodes(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        text = "🔗 <b>VLESS / HY2</b>\n\nВыбери ноду:"
        exit_nodes = self.nm.exit_nodes()
        node_btns = [
            (f"{_flag(n.region)} {n.display_name}", f"sub_node:{n.name}")
            for n in exit_nodes
        ]
        rows = [node_btns[i:i+2] for i in range(0, len(node_btns), 2)]
        rows.append([("📱 AmneziaVPN (xray)", "ax_nodes")])
        rows.append([("📋 Вся подписка", "sub_full"), ("🏠 Меню", "menu")])
        await self._show(chat_id, msg_id, text, _kb(rows))

    async def _sub_protos(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Генерирую URI для <b>{node.display_name}</b>...")

        try:
            uris = await self._run(self._get_node_uris, node)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        if not uris:
            await self._edit(chat_id, msg_id, "❌ URI не найдены (нет секретов?)")
            return

        text = f"🔗 <b>{node.display_name}</b> {_flag(node.region)}\n\nВыбери подключение:"
        proto_btns = [
            (label, f"sub_uri:{node.name}:{key}")
            for key, label, _ in uris
        ]
        rows = [proto_btns[i:i+2] for i in range(0, len(proto_btns), 2)]
        rows.append([("← Ноды", "sub"), ("🏠 Меню", "menu")])
        await self._edit(chat_id, msg_id, text, _kb(rows))

    async def _sub_uri_show(
        self, chat_id: int | str, msg_id: int, node_name: str, type_key: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        try:
            uris = await self._run(self._get_node_uris, node)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        entry = next((u for u in uris if u[0] == type_key), None)
        if not entry:
            await self._edit(chat_id, msg_id, f"❌ URI для '{type_key}' не найден.")
            return

        key, label, uri = entry
        text = (
            f"🔗 <b>{label}</b>\n\n"
            f"Нажми на URI чтобы скопировать:\n"
            f"<code>{uri}</code>"
        )
        markup = _kb([[(f"← {node.display_name}", f"sub_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    async def _sub_full(self, chat_id: int | str, msg_id: int) -> None:
        sub_cfg = self.cfg.get("subscription", {})
        base_url = sub_cfg.get("base_url", "").rstrip("/")
        sub_uuid = os.environ.get("SUBSCRIPTION_UUID", "")
        if base_url and sub_uuid:
            url = f"{base_url}/{sub_uuid}"
        else:
            url = base_url or "URL не настроен"

        text = (
            "📋 <b>Полная V2Ray подписка</b>\n\n"
            "Содержит все маршруты (VLESS + HY2 + бриджи).\n\n"
            "Нажми чтобы скопировать:\n"
            f"<code>{url}</code>"
        )
        markup = _kb([[("← Подписки", "sub"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # AmneziaVPN (xray) — vpn:// links for VLESS+Reality
    # ------------------------------------------------------------------

    async def _amnezia_xray_nodes(
        self, chat_id: int | str, msg_id: int,
    ) -> None:
        exit_nodes = self.nm.exit_nodes()
        text = "📱 <b>AmneziaVPN (xray)</b>\n\nВыбери ноду:"
        btns = [
            (f"{_flag(n.region)} {n.display_name}", f"ax_node:{n.name}")
            for n in exit_nodes
            if "vless_reality" in n.protocols
        ]
        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("← Подписки", "sub"), ("🏠 Меню", "menu")])
        await self._edit(chat_id, msg_id, text, _kb(rows))

    async def _amnezia_xray_routes(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        from core.bot._helpers import _flag as flag_fn
        from core.config_gen import BRIDGE_SHORT as _BS

        text = (
            f"📱 <b>{node.display_name}</b> {_flag(node.region)}\n\n"
            "Выбери маршрут:"
        )
        rows = [[(f"🔗 Прямая", f"ax_link:{node_name}:direct")]]
        bridge_row = []
        for bridge in self.nm.bridge_nodes():
            if not bridge.enabled:
                continue
            label = _BS.get(bridge.name, bridge.display_name)
            bridge_row.append(
                (f"🌐 {label}", f"ax_link:{node_name}:{bridge.name}")
            )
            if len(bridge_row) == 3:
                rows.append(bridge_row)
                bridge_row = []
        if bridge_row:
            rows.append(bridge_row)

        rows.append([("← Ноды", "ax_nodes"), ("🏠 Меню", "menu")])
        await self._edit(chat_id, msg_id, text, _kb(rows))

    async def _amnezia_xray_link(
        self, chat_id: int | str, msg_id: int,
        node_name: str, route: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, "⏳ Генерирую vpn:// ссылку...")

        try:
            link = await self._run(
                lambda: self._get_amnezia_xray_link(node_name, route)
            )
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        route_label = "Прямая" if route == "direct" else f"via {route}"
        text = (
            f"📱 <b>{node.display_name} / {route_label}</b>\n\n"
            "Нажми чтобы скопировать:\n"
            f"<code>{link}</code>"
        )
        markup = _kb([[(f"← Маршруты", f"ax_node:{node_name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    def _get_amnezia_xray_link(self, node_name: str, route: str) -> str:
        from core.config_gen import ConfigGenerator, BRIDGE_SHORT as _BS

        cg = ConfigGenerator()
        global_cfg = cg._load_global()
        bridge_cfg = global_cfg.get("bridge", {})
        node = self.nm.get_node(node_name)
        secrets = cg.load_secrets(node_name)

        if route == "direct":
            return cg.generate_amnezia_xray_vpn_link(node, secrets)

        bridge = self.nm.get_node(route)
        bs = cg.load_secrets(bridge.name)

        if bridge.single_inbound_port:
            node_clients = bs.get("node_clients", {})
            nc = node_clients.get(node.name, {})
            b_uuid = nc.get("uuid", "")
            b_port = bridge.single_inbound_port
        else:
            b_uuid = bs.get("bridge_access_uuid", "")
            b_port = bridge.inbound_port_start + node.bridge_port_offset

        return cg.generate_amnezia_xray_vpn_link(
            node, secrets,
            relay_host=bridge.ip,
            relay_port=b_port,
            relay_sni=bs.get("reality_server_name", ""),
            relay_pbk=bs.get("reality_public_key", ""),
            relay_sid=bs.get("reality_short_id", ""),
            relay_uuid=b_uuid,
            relay_label=_BS.get(bridge.name, bridge.display_name),
        )

    # ------------------------------------------------------------------
    # URI generation (blocking, for executor)
    # ------------------------------------------------------------------

    def _get_node_uris(self, node) -> list[tuple[str, str, str]]:
        """Returns [(type_key, display_label, full_uri), ...] for exit node."""
        from core.config_gen import ConfigGenerator

        cg = ConfigGenerator()
        bridge_nodes = self.nm.bridge_nodes()
        raw = cg.generate_subscription_plain([node], bridge_nodes)

        result = []
        for uri in raw:
            if "#" in uri:
                label = urllib.parse.unquote(uri.split("#", 1)[1])
            else:
                label = uri[:30]
            key = self._label_to_key(label)
            result.append((key, label, uri))
        return result

    @staticmethod
    def _label_to_key(label: str) -> str:
        parts = label.split()
        for part in reversed(parts):
            if part.isdigit():
                continue
            ascii_only = "".join(c for c in part if c.isascii())
            if ascii_only:
                return ascii_only.lower()
        return label[:10].lower()
