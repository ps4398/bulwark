"""Management menu, bridge/sub push, portal, failover, upgrades."""

from __future__ import annotations

import asyncio
import json as _json_mod
import os
import time as _time_mod
import urllib.parse as _urlparse
from datetime import datetime as _dt
from typing import Optional

import aiohttp

from core.bot._helpers import _PROJECT_ROOT, _flag, _kb, _now_utc
from core.config_gen import BRIDGE_SHORT as _BRIDGE_SHORT


class MgmtMixin:
    """Management, portal, failover, upgrades."""

    # ------------------------------------------------------------------
    # Management menu
    # ------------------------------------------------------------------

    async def _mgmt_menu(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        text = "⚙️ <b>Управление</b>\n\nВыбери действие:"
        markup = _kb([
            [("🔄 Bridge Push", "bridge_push"), ("📤 Sub Push", "sub_push")],
            [("⚡ Ручной Failover", "failover"), ("🌐 Портал", "portal_check")],
            [("🔧 Обновления", "upgrades"), ("🖥️ Ноды", "node_mgmt")],
            [("🏠 Меню", "menu")],
        ])
        await self._show(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # Bridge push
    # ------------------------------------------------------------------

    async def _bridge_push(self, chat_id: int | str, msg_id: int) -> None:
        if not self.bm:
            await self._edit(chat_id, msg_id, "❌ BridgeManager не инициализирован.")
            return

        await self._edit(chat_id, msg_id, "⏳ Пушу конфиг на все бриджи...")

        try:
            await self._run(self.bm.update_bridge)
            result = "✅ Bridge конфиг обновлён на всех нодах."
        except Exception as e:
            result = f"❌ Ошибка: {e}"

        markup = _kb([[("← Управление", "mgmt"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Subscription push
    # ------------------------------------------------------------------

    async def _do_sub_push(self, chat_id: int | str, msg_id: int) -> None:
        sub_uuid = os.environ.get("SUBSCRIPTION_UUID", "")
        if not sub_uuid:
            await self._edit(chat_id, msg_id, "❌ SUBSCRIPTION_UUID не задан в .env")
            return

        await self._edit(chat_id, msg_id, "⏳ Генерирую подписку и пушу на портал...")

        def _push() -> tuple[int, int]:
            from core.config_gen import ConfigGenerator
            cg = ConfigGenerator()
            exit_nodes = self.nm.exit_nodes()
            bridge_nodes = self.nm.bridge_nodes()

            content = cg.generate_subscription(exit_nodes, bridge_nodes=bridge_nodes)
            raw_uris = cg.generate_subscription_plain(exit_nodes, bridge_nodes=bridge_nodes)

            conn_entries = []
            for uri in raw_uris:
                label = _urlparse.unquote(uri.split("#")[-1]) if "#" in uri else ""
                proto = uri.split("://")[0]
                parts = label.split(" | ")
                conn_entries.append({
                    "uri": uri,
                    "label": label,
                    "protocol": proto,
                    "region": parts[1].strip() if len(parts) > 1 else "",
                    "node": parts[2].strip() if len(parts) > 2 else "",
                    "type": parts[3].strip() if len(parts) > 3 else "",
                    "via_bridge": "via" in label.lower(),
                })
            connections_json = _json_mod.dumps({
                "updated_at": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "uris": conn_entries,
            }, ensure_ascii=False, indent=2)

            global_cfg = cg._load_global()
            global_cfg_awg = global_cfg.get("amneziawg", {})
            bridge_cfg = global_cfg.get("bridge", {})

            awg_nodes: dict = {}
            for node in exit_nodes:
                secrets = cg.load_secrets(node.name)
                if not secrets.get("awg_public_key"):
                    continue
                peers = secrets.get("awg_peers", [])
                vpn_links = []
                for peer in peers:
                    if not peer.get("private_key"):
                        continue
                    direct = cg.generate_amneziawg_vpn_link(node, secrets, peer)
                    peer_entry = {
                        "name": peer.get("name", "default"),
                        "address": peer.get("address", ""),
                        "vpn_link": direct,
                        "bridge_links": {},
                    }
                    for bridge in bridge_nodes:
                        if not bridge.enabled:
                            continue
                        label = _BRIDGE_SHORT.get(bridge.name, bridge.display_name)
                        if bridge.single_inbound_port:
                            ps = int(bridge_cfg.get("awg_relay_port_start_single", 51821))
                        else:
                            ps = int(bridge_cfg.get("awg_relay_port_start", 24441))
                        bl = cg.generate_amneziawg_vpn_link(
                            node, secrets, peer,
                            relay_host=bridge.ip,
                            relay_port=ps + node.bridge_port_offset,
                            relay_label=label,
                        )
                        peer_entry["bridge_links"][label] = bl
                    vpn_links.append(peer_entry)
                awg_nodes[node.name] = {
                    "display_name": node.display_name,
                    "region": node.region.upper(),
                    "endpoint": f"{node.ip}:51820",
                    "public_key": secrets["awg_public_key"],
                    "jc": int(global_cfg_awg.get("jc", 4)),
                    "jmin": int(global_cfg_awg.get("jmin", 40)),
                    "jmax": int(global_cfg_awg.get("jmax", 70)),
                    "s1": int(secrets.get("awg_s1", 0)),
                    "s2": int(secrets.get("awg_s2", 0)),
                    "s3": int(secrets.get("awg_s3", 0)),
                    "s4": int(secrets.get("awg_s4", 0)),
                    "h1": secrets.get("awg_h1", 1),
                    "h2": secrets.get("awg_h2", 2),
                    "h3": secrets.get("awg_h3", 3),
                    "h4": secrets.get("awg_h4", 4),
                    "peers": vpn_links,
                }
            awg_json = _json_mod.dumps(awg_nodes, ensure_ascii=False, indent=2)

            runtime_dir = _PROJECT_ROOT / "deploy" / "portal" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "subscription.b64").write_text(content, encoding="utf-8")
            (runtime_dir / "connections.json").write_text(connections_json, encoding="utf-8")
            (runtime_dir / "awg_info.json").write_text(awg_json, encoding="utf-8")

            return len(raw_uris), len(awg_nodes)

        try:
            n_uris, n_awg = await self._run(_push)
            result = f"✅ Подписка обновлена на портале\n{n_uris} URI · {n_awg} AWG нод"
        except Exception as e:
            result = f"❌ Ошибка: {e}"

        markup = _kb([[("← Управление", "mgmt"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Portal check / reload
    # ------------------------------------------------------------------

    async def _portal_check(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        msg_id = await self._show(chat_id, msg_id, "⏳ Проверяю портал...")

        sub_uuid = os.environ.get("SUBSCRIPTION_UUID", "")
        sub_cfg = self.cfg.get("subscription", {})
        base_url = sub_cfg.get("base_url", "").rstrip("/")
        check_url = f"{base_url}/{sub_uuid}" if sub_uuid else base_url

        session = await self._get_http_session()
        t0 = _time_mod.monotonic()
        status_code, size, err_text = None, 0, ""
        try:
            async with session.get(
                check_url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                elapsed = _time_mod.monotonic() - t0
                status_code = r.status
                body = await r.read()
                size = len(body)
        except asyncio.TimeoutError:
            elapsed = _time_mod.monotonic() - t0
            err_text = "timeout (>15s)"
        except Exception as e:
            elapsed = _time_mod.monotonic() - t0
            err_text = str(e)[:80]

        if status_code == 200:
            icon = "🟢"
            status_line = f"HTTP {status_code} · {elapsed * 1000:.0f}ms · {size / 1024:.1f} KB"
        elif status_code:
            icon = "🟡"
            status_line = f"HTTP {status_code} · {elapsed * 1000:.0f}ms"
        else:
            icon = "🔴"
            status_line = f"Недоступен — {err_text}"

        text = (
            f"🌐 <b>Портал</b>\n"
            f"<code>{check_url}</code>\n\n"
            f"{icon} {status_line}\n\n"
            f"<i>{_now_utc()}</i>"
        )
        markup = _kb([
            [("🔄 Проверить", "portal_check"), ("⟳ Reload Gunicorn", "portal_reload")],
            [("← Управление", "mgmt")],
        ])
        await self._edit(chat_id, msg_id, text, markup)

    async def _portal_reload(self, chat_id: int | str, msg_id: int) -> None:
        await self._edit(chat_id, msg_id, "⏳ Перезапускаю портал...")

        try:
            proc = await self._run(
                lambda: __import__("subprocess").run(
                    ["systemctl", "restart", "bulwark-portal"],
                    capture_output=True, text=True, timeout=15,
                ),
            )
            if proc.returncode == 0:
                result = "✅ Портал перезапущен"
            else:
                result = f"⚠️ rc={proc.returncode}\n<code>{(proc.stderr or proc.stdout)[:300]}</code>"
        except Exception as e:
            result = f"❌ Ошибка: {e}"

        markup = _kb([[("← Портал", "portal_check"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Failover
    # ------------------------------------------------------------------

    async def _failover_menu(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        exit_nodes = self.nm.exit_nodes()
        text = "⚡ <b>Ручной Failover</b>\n\nВыбери ноду <i>источник</i> (с которой перенаправить трафик):"
        btns = [
            (f"{_flag(n.region)} {n.display_name}", f"failover_src:{n.name}")
            for n in exit_nodes
        ]
        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("← Управление", "mgmt")])
        await self._show(chat_id, msg_id, text, _kb(rows))

    async def _failover_select(
        self, chat_id: int | str, msg_id: int, src_name: str,
    ) -> None:
        exit_nodes = self.nm.exit_nodes()
        text = f"⚡ <b>Failover из {src_name}</b>\n\nВыбери <i>резервную</i> ноду:"
        btns = [
            (f"{_flag(n.region)} {n.display_name}", f"failover_cfg:{src_name}:{n.name}")
            for n in exit_nodes if n.name != src_name
        ]
        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("← Failover", "failover")])
        await self._edit(chat_id, msg_id, text, _kb(rows))

    async def _failover_confirm(
        self, chat_id: int | str, msg_id: int, src_name: str, dst_name: str,
    ) -> None:
        text = (
            f"⚠️ <b>Подтвердить Failover?</b>\n\n"
            f"Переключить трафик:\n"
            f"<code>{src_name}</code> → <code>{dst_name}</code>\n\n"
            "Bridge конфиг будет перегенерирован и отправлен на все бриджи."
        )
        markup = _kb([[
            ("✅ Выполнить", f"failover_exec:{src_name}:{dst_name}"),
            ("❌ Отмена", "failover"),
        ]])
        await self._edit(chat_id, msg_id, text, markup)

    async def _failover_exec(
        self, chat_id: int | str, msg_id: int, src_name: str, dst_name: str,
    ) -> None:
        if not self.bm:
            await self._edit(chat_id, msg_id, "❌ BridgeManager не инициализирован.")
            return

        try:
            failed_node = self.nm.get_node(src_name)
            backup_node = self.nm.get_node(dst_name)
        except KeyError as e:
            await self._edit(chat_id, msg_id, f"❌ Нода не найдена: {e}")
            return

        await self._edit(
            chat_id, msg_id,
            f"⏳ Выполняю failover: <code>{src_name}</code> → <code>{dst_name}</code>...",
        )

        try:
            await self._run(self.bm.failover, failed_node, backup_node)
            result = (
                f"✅ Failover выполнен\n"
                f"<code>{src_name}</code> → <code>{dst_name}</code>"
            )
        except Exception as e:
            result = f"❌ Ошибка failover: {e}"

        markup = _kb([[("← Управление", "mgmt"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Upgrades
    # ------------------------------------------------------------------

    async def _upgrades_menu(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        exit_nodes = self.nm.exit_nodes()
        text = "🔧 <b>Обновления бинарников</b>\n\nВыбери ноду:"
        btns = [
            (f"{_flag(n.region)} {n.display_name}", f"upgrade_node:{n.name}")
            for n in exit_nodes
        ]
        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("← Управление", "mgmt")])
        await self._show(chat_id, msg_id, text, _kb(rows))

    async def _upgrade_node_screen(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Проверяю версии на <code>{node.name}</code>...")

        try:
            cur_xray, cur_hy2, latest_xray, latest_hy2 = await self._run(
                self._fetch_versions, node,
            )
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        def _ver_line(binary: str, current: str, latest: str) -> str:
            if not current:
                return f"{binary}: <i>не установлен</i>"
            if latest and current != latest:
                return f"{binary}: <code>{current}</code> → <code>{latest}</code> ⬆️"
            return f"{binary}: <code>{current}</code> ✓"

        lines = [
            f"🔧 <b>Обновления — {node.name}</b>\n",
            _ver_line("xray", cur_xray, latest_xray),
            _ver_line("hysteria2", cur_hy2, latest_hy2),
            f"\n<i>{_now_utc()}</i>",
        ]

        upgrade_row: list[tuple[str, str]] = []
        if cur_xray and latest_xray and cur_xray != latest_xray:
            upgrade_row.append((f"⬆️ xray → {latest_xray}", f"upgrade_exec:{node.name}:xray"))
        if cur_hy2 and latest_hy2 and cur_hy2 != latest_hy2:
            upgrade_row.append((f"⬆️ hy2 → {latest_hy2}", f"upgrade_exec:{node.name}:hy2"))

        kb_rows: list[list[tuple[str, str]]] = []
        if upgrade_row:
            kb_rows.append(upgrade_row)
        kb_rows.append([
            ("🔄 Обновить", f"upgrade_node:{node.name}"),
            ("← Ноды", "upgrades"),
        ])
        await self._edit(chat_id, msg_id, "\n".join(lines), _kb(kb_rows))

    def _fetch_versions(self, node) -> tuple[str, str, str, str]:
        """Blocking: SSH (current) + GitHub cache (latest)."""
        import json as _json
        import time as _time
        import urllib.request as _req

        hy2_bin = self.cfg.get("hysteria2", {}).get("binary", "/usr/local/bin/hysteria")

        cmd = (
            f"echo XR:$(xray version 2>/dev/null | awk 'NR==1{{print $2}}'); "
            f"echo HY:$({hy2_bin} version 2>&1 | grep -m1 '^Version:' | awk '{{print $2}}')"
        )
        out, _, _ = self.nm.exec_command(node, cmd, timeout=15)

        cur_xray, cur_hy2 = "", ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("XR:"):
                cur_xray = line[3:].strip()
            elif line.startswith("HY:"):
                val = line[3:].strip()
                if val:
                    cur_hy2 = val.split()[-1].lstrip("v")

        now = _time.monotonic()
        cache_ttl = 1800.0

        def _get_cached(key: str) -> str:
            entry = self._github_cache.get(key)
            if entry and (now - entry[1]) < cache_ttl:
                return entry[0]
            return ""

        def _gh_latest(repo: str) -> str:
            try:
                req = _req.Request(
                    f"https://api.github.com/repos/{repo}/releases/latest",
                    headers={"User-Agent": "bulwark-bot"},
                )
                with _req.urlopen(req, timeout=8) as resp:
                    tag = _json.loads(resp.read()).get("tag_name", "")
                if "/" in tag:
                    tag = tag.rsplit("/", 1)[-1]
                return tag.lstrip("v")
            except Exception:
                return ""

        latest_xray = _get_cached("xray") or _gh_latest("XTLS/Xray-core")
        if latest_xray:
            self._github_cache["xray"] = (latest_xray, now)

        latest_hy2 = _get_cached("hy2") or _gh_latest("apernet/hysteria")
        if latest_hy2:
            self._github_cache["hy2"] = (latest_hy2, now)

        return cur_xray, cur_hy2, latest_xray, latest_hy2

    async def _do_upgrade_binary(
        self, chat_id: int | str, msg_id: int, node_name: str, binary: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(
            chat_id, msg_id,
            f"⏳ Обновляю <b>{binary}</b> на <code>{node.name}</code>...\n"
            "<i>Это займёт ~30 секунд</i>",
        )

        if binary == "xray":
            cmd = (
                "bash <(curl -sSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)"
                " @ 2>/dev/null && systemctl restart xray && xray version | head -1"
            )
        elif binary == "hy2":
            cmd = (
                "curl -fsSL https://get.hy2.sh/ | bash 2>/dev/null"
                " && systemctl restart hysteria2 && hysteria version | head -1"
            )
        else:
            await self._edit(chat_id, msg_id, f"❌ Неизвестный бинарник: {binary}")
            return

        self._github_cache.pop(binary, None)

        try:
            out, err, rc = await self._ssh(node, cmd, timeout=120)
            if rc == 0:
                ver_line = out.strip().splitlines()[-1] if out.strip() else "?"
                result = f"✅ <b>{binary}</b> обновлён\n<code>{ver_line}</code>"
            else:
                result = f"❌ rc={rc}\n<code>{(err or out)[:400]}</code>"
        except Exception as e:
            result = f"❌ SSH ошибка: {e}"

        markup = _kb([[(f"← {node.name}", f"upgrade_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)
