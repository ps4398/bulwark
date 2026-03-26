"""AmneziaWG peer management handlers."""

from __future__ import annotations

import os
from typing import Optional

from core.bot._helpers import _flag, _kb
from core.config_gen import BRIDGE_SHORT as _BRIDGE_SHORT


class AWGMixin:
    """AWG node list, peer display, link generation, peer creation."""

    # ------------------------------------------------------------------
    # AWG node list
    # ------------------------------------------------------------------

    async def _awg_nodes(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        exit_nodes = [
            n for n in self.nm.exit_nodes()
            if "amneziawg" in n.protocols
        ]
        text = "🛡 <b>AmneziaWG</b>\n\nВыбери ноду или получи подписку:"
        btns = [
            (f"{_flag(n.region)} {n.display_name}", f"awg_peers:{n.name}")
            for n in exit_nodes
        ]
        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("📋 AWG Подписка", "awg_sub_link")])
        rows.append([("🏠 Меню", "menu")])
        await self._show(chat_id, msg_id, text, _kb(rows))

    # ------------------------------------------------------------------
    # Peer list for a node
    # ------------------------------------------------------------------

    async def _awg_peers(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        try:
            peers = await self._run(self._load_awg_peers, node_name)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        if not peers:
            await self._edit(chat_id, msg_id, "❌ AWG пиры не найдены (нет секретов?).")
            return

        lines = [f"🛡 <b>AWG — {node.display_name}</b>\n"]
        for i, p in enumerate(peers):
            lines.append(f"Пир {i + 1}: <b>{p.get('name', '?')}</b>  ({p.get('address', '?')})")

        peer_btns = [
            (f"🔑 {p.get('name', str(i))}",
             f"awg_peer:{node.name}:{p.get('address', '').split('.')[-1]}")
            for i, p in enumerate(peers)
        ]
        rows = [peer_btns[i:i+2] for i in range(0, len(peer_btns), 2)]
        rows.append([("➕ Добавить пира", f"awg_add:{node.name}")])
        rows.append([("← Ноды", "awg_nodes"), ("🏠 Меню", "menu")])
        await self._edit(chat_id, msg_id, "\n".join(lines), _kb(rows))

    # ------------------------------------------------------------------
    # Route selection for peer
    # ------------------------------------------------------------------

    @staticmethod
    def _find_peer_by_addr(peers: list[dict], addr_id: str):
        for i, p in enumerate(peers):
            addr = p.get("address", "")
            if addr.split(".")[-1] == addr_id:
                return i, p
        return None

    async def _awg_peer_show(
        self, chat_id: int | str, msg_id: int, node_name: str, peer_id: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        peers = await self._run(self._load_awg_peers, node_name)
        found = self._find_peer_by_addr(peers, peer_id)
        if not found:
            await self._edit(chat_id, msg_id, "❌ Пир не найден.")
            return
        _, peer = found
        peer_name = peer.get("name", "?")
        peer_addr = peer.get("address", "?")

        text = (
            f"🔑 <b>{node.display_name} / {peer_name}</b>  ({peer_addr})\n\n"
            "Выберите маршрут:"
        )

        rows = [[(f"🔗 Прямая", f"awg_link:{node_name}:{peer_id}:direct")]]
        bridge_row = []
        for bridge in self.nm.bridge_nodes():
            if not bridge.enabled:
                continue
            label = _BRIDGE_SHORT.get(bridge.name, bridge.display_name)
            bridge_row.append(
                (f"🌐 {label}", f"awg_link:{node_name}:{peer_id}:{bridge.name}")
            )
            if len(bridge_row) == 3:
                rows.append(bridge_row)
                bridge_row = []
        if bridge_row:
            rows.append(bridge_row)

        rows.append([(f"← Пиры", f"awg_peers:{node_name}"), ("🏠 Меню", "menu")])
        await self._edit(chat_id, msg_id, text, _kb(rows))

    # ------------------------------------------------------------------
    # vpn:// link display
    # ------------------------------------------------------------------

    async def _awg_link_show(
        self, chat_id: int | str, msg_id: int,
        node_name: str, peer_id: str, route: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        peers = await self._run(self._load_awg_peers, node_name)
        found = self._find_peer_by_addr(peers, peer_id)
        if not found:
            await self._edit(chat_id, msg_id, "❌ Пир не найден.")
            return
        peer_idx, peer = found

        await self._edit(chat_id, msg_id, "⏳ Генерирую vpn:// ссылку...")

        try:
            link = await self._run(
                lambda: self._get_awg_vpn_link_route(node_name, peer_idx, route)
            )
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        route_label = "Прямая" if route == "direct" else f"via {route}"
        text = (
            f"🔑 <b>{node.display_name} / {route_label}</b>\n\n"
            "Нажми чтобы скопировать:\n"
            f"<code>{link}</code>"
        )
        markup = _kb([[(f"← Маршруты", f"awg_peer:{node_name}:{peer_id}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # AWG subscription link
    # ------------------------------------------------------------------

    async def _awg_sub_link(
        self, chat_id: int | str, msg_id: int,
    ) -> None:
        from core.awg_users import AWGUserManager
        from core.config_gen import ConfigGenerator

        def _gen():
            mgr = AWGUserManager()
            users = mgr.list_users()
            active = [u for u in users if u.get("active") and u.get("api_key")]
            if not active:
                return None, "Нет активных AWG подписок.\nСоздайте: <code>bulwark awg sub add имя</code>"
            user = active[0]
            sub_base = self.cfg.get("subscription", {}).get("base_url", "").rstrip("/")
            portal_base = (
                sub_base.rsplit("/", 1)[0]
                if "/" in sub_base.lstrip("https://").lstrip("http://")
                else sub_base
            )
            awg_prefix = os.environ.get("PORTAL_AWG_PREFIX", "/awg-api")
            link = ConfigGenerator.generate_awg_subscription_link(
                api_key=user["api_key"],
                base_url=portal_base,
                awg_prefix=awg_prefix,
            )
            return user, link

        try:
            user, result = await self._run(_gen)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        if user is None:
            text = f"📋 <b>AWG Подписка</b>\n\n{result}"
        else:
            nodes_str = ", ".join(user.get("assigned_nodes", []))
            text = (
                f"📋 <b>AWG Подписка</b>\n\n"
                f"Серверы: {nodes_str}\n"
                f"Устройств: {len(user.get('peers', []))} / {user.get('max_peers', 5)}\n\n"
                "Импортируй в AmneziaVPN — сервер выбирается автоматически.\n"
                "При падении ноды — переимпортируй для переключения.\n\n"
                f"<code>{result}</code>"
            )

        markup = _kb([[("← AmneziaWG", "awg_nodes"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # Confirm / execute peer creation
    # ------------------------------------------------------------------

    async def _awg_add_peer_confirm(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        peers = await self._run(self._load_awg_peers, node_name)
        next_n = len(peers) + 1
        max_last = 1
        for p in peers:
            addr = p.get("address", "")
            if addr:
                try:
                    max_last = max(max_last, int(addr.split(".")[-1]))
                except ValueError:
                    pass
        from core.config_gen import REGION_OCTET
        region_octet = REGION_OCTET.get(node.region, 99)
        next_ip = f"10.{region_octet}.0.{max_last + 1}"

        text = (
            f"➕ <b>Добавить AWG пира — {node.name}</b>\n\n"
            f"Имя: <code>peer_{next_n}</code>\n"
            f"IP: <code>{next_ip}</code>\n\n"
            "Ключи будут сгенерированы на сервере."
        )
        markup = _kb([[
            ("✅ Добавить", f"awg_add_exec:{node.name}"),
            ("❌ Отмена", f"awg_peers:{node.name}"),
        ]])
        await self._edit(chat_id, msg_id, text, markup)

    async def _awg_add_peer_exec(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, "⏳ Генерирую ключи и добавляю пира...")

        try:
            vpn_link = await self._run(self._do_awg_add_peer, node)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        text = (
            "✅ <b>Пир добавлен!</b>\n\n"
            "vpn:// ссылка (нажми скопировать):\n"
            f"<code>{vpn_link}</code>"
        )
        markup = _kb([[(f"← Пиры {node.name}", f"awg_peers:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # AWG blocking helpers (for executor)
    # ------------------------------------------------------------------

    def _load_awg_peers(self, node_name: str) -> list[dict]:
        from core.config_gen import ConfigGenerator
        cg = ConfigGenerator()
        secrets = cg.load_secrets(node_name)
        return secrets.get("awg_peers", [])

    def _get_awg_vpn_link_route(
        self, node_name: str, peer_idx: int, route: str,
    ) -> str:
        from core.config_gen import ConfigGenerator
        cg = ConfigGenerator()
        global_cfg = cg._load_global()
        bridge_cfg = global_cfg.get("bridge", {})
        node = self.nm.get_node(node_name)
        secrets = cg.load_secrets(node_name)
        peers = secrets.get("awg_peers", [])
        if peer_idx >= len(peers):
            raise IndexError(f"Пир {peer_idx} не найден (всего {len(peers)})")
        peer = peers[peer_idx]

        if route == "direct":
            return cg.generate_amneziawg_vpn_link(node, secrets, peer)

        bridge = self.nm.get_node(route)
        if bridge.single_inbound_port:
            port_start = int(bridge_cfg.get("awg_relay_port_start_single", 51821))
        else:
            port_start = int(bridge_cfg.get("awg_relay_port_start", 24441))

        return cg.generate_amneziawg_vpn_link(
            node, secrets, peer,
            relay_host=bridge.ip,
            relay_port=port_start + node.bridge_port_offset,
            relay_label=_BRIDGE_SHORT.get(bridge.name, bridge.display_name),
        )

    def _do_awg_add_peer(self, node) -> str:
        """Generate keys on node, add peer, push config. Returns vpn:// link."""
        with self._get_peer_lock(node.name):
            return self._do_awg_add_peer_impl(node)

    def _do_awg_add_peer_impl(self, node) -> str:
        import yaml as _yaml
        from core.config_gen import ConfigGenerator, REGION_OCTET

        cg = ConfigGenerator()
        secrets = cg.load_secrets(node.name)
        peers: list[dict] = secrets.get("awg_peers", [])

        max_last = 1
        for p in peers:
            addr = p.get("address", "")
            try:
                max_last = max(max_last, int(addr.split(".")[-1]))
            except (ValueError, IndexError):
                pass
        region_octet = REGION_OCTET.get(node.region, 99)
        next_ip = f"10.{region_octet}.0.{max_last + 1}"
        peer_name = f"peer_{len(peers) + 1}"

        with self.nm.ssh(node) as conn:
            out, err, rc = conn.exec("awg genkey")
            if rc != 0:
                out, err, rc = conn.exec("wg genkey")
            if rc != 0:
                raise RuntimeError(f"Key generation failed: {err}")
            priv_key = out.strip()

            conn.exec(f"printf '%s' {priv_key!r} > /tmp/df_awg_client.tmp")
            pub_out, _, pub_rc = conn.exec(
                "awg pubkey < /tmp/df_awg_client.tmp"
                " || wg pubkey < /tmp/df_awg_client.tmp"
            )
            conn.exec("rm -f /tmp/df_awg_client.tmp")
            if pub_rc != 0:
                raise RuntimeError("Failed to derive public key")
            pub_key = pub_out.strip()

            new_peer = {
                "name": peer_name,
                "address": next_ip,
                "private_key": priv_key,
                "public_key": pub_key,
            }
            peers.append(new_peer)
            secrets["awg_peers"] = peers

            if "outbound_iface" not in secrets:
                iface_out, _, _ = conn.exec(
                    "ip route | awk '/default/{print $5; exit}'"
                )
                iface = iface_out.strip() or "eth0"
                secrets["outbound_iface"] = iface

            path = cg.secrets_dir / f"{node.name}.yaml"
            with open(path, "w", encoding="utf-8") as fh:
                _yaml.dump(
                    dict(sorted(secrets.items())), fh,
                    allow_unicode=True, default_flow_style=False,
                )

            awg_cfg_content = cg.generate_amneziawg(node, secrets)
            awg_cfg_path = cg._load_global().get("amneziawg_server", {}).get(
                "config_path", "/etc/amnezia/amneziawg/awg0.conf"
            )
            conn.upload_content(awg_cfg_content, awg_cfg_path)
            conn.exec("chmod 600 " + awg_cfg_path)
            _, restart_err, restart_rc = conn.exec(
                "systemctl restart wg-quick@awg0", timeout=15,
            )
            if restart_rc != 0:
                raise RuntimeError(
                    f"AWG restart failed (rc={restart_rc}): {restart_err}"
                )

        return cg.generate_amneziawg_vpn_link(node, secrets, new_peer)
