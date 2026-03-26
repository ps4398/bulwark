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
