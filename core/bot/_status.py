"""Status display, service management, system info, speedtest, cleanup."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from core.bot._helpers import _flag, _kb, _now_utc


class StatusMixin:
    """Node status, restart, logs, sysinfo, svc_status, reboot, cleanup, speedtest, stats."""

    # ------------------------------------------------------------------
    # Status — all nodes
    # ------------------------------------------------------------------

    async def _status_all(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        msg_id = await self._show(chat_id, msg_id, "⏳ Проверяю ноды...")

        if self.monitor:
            cached = self.monitor.all_statuses()
            if cached:
                statuses, source = cached, "кеш"
            else:
                nodes = self.nm.enabled_nodes()
                results = await asyncio.gather(
                    *[self.monitor.run_checks(n) for n in nodes],
                    return_exceptions=True,
                )
                statuses = {
                    n.name: r
                    for n, r in zip(nodes, results)
                    if not isinstance(r, Exception)
                }
                source = "проверка"
        else:
            statuses, source = {}, "нет монитора"

        lines = [f"<b>Статус нод</b> ({source}):"]
        for node in self.nm.all_nodes():
            st = statuses.get(node.name)
            if st is None:
                lines.append(f"\n⚪ <b>{node.name}</b>  нет данных")
                continue
            h = "🟢" if st.overall_healthy else "🔴"
            icmp = f"{st.icmp_latency_ms:.0f}ms" if st.icmp_latency_ms else "✗"
            protos = "  ".join(
                ("✓" if ps.healthy else "✗") + " " + name
                for name, ps in st.protocol_statuses.items()
            )
            lines.append(f"\n{h} <b>{node.name}</b>  ICMP {icmp}")
            if protos:
                lines.append(f"   {protos}")

        lines.append(f"\n<i>{_now_utc()}</i>")

        all_nodes = self.nm.all_nodes()
        node_btns: list[tuple[str, str]] = []
        for n in all_nodes:
            st = statuses.get(n.name)
            h = "🟢" if (st and st.overall_healthy) else ("🔴" if st else "⚪")
            role = "🌉" if n.is_bridge else "🚀"
            node_btns.append((f"{h}{role} {n.name}", f"status_node:{n.name}"))

        rows = [node_btns[i:i+2] for i in range(0, len(node_btns), 2)]
        rows.append([("🔄 Обновить", "status"), ("🏠 Меню", "menu")])
        await self._edit(chat_id, msg_id, "\n".join(lines), _kb(rows))

    # ------------------------------------------------------------------
    # Status — single node detail
    # ------------------------------------------------------------------

    async def _status_node(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Проверяю <code>{node.name}</code>...")

        if not self.monitor:
            await self._edit(chat_id, msg_id, "❌ Монитор не инициализирован.")
            return

        st = await self.monitor.run_checks(node)

        h = "🟢" if st.overall_healthy else "🔴"
        lines = [
            f"{h} <b>{node.name}</b> {_flag(node.region)}",
            f"<code>{node.ip}</code>  ·  {node.role}",
        ]
        icmp_icon = "✓" if st.icmp_ok else "✗"
        icmp_lat = f"  {st.icmp_latency_ms:.1f}ms" if st.icmp_latency_ms else ""
        lines.append(f"\nICMP: {icmp_icon}{icmp_lat}")

        for proto, ps in st.protocol_statuses.items():
            p = "✓" if ps.healthy else "✗"
            lat = f"  {ps.latency_ms:.1f}ms" if ps.latency_ms else ""
            err = f"\n   {ps.error}" if ps.error and not ps.healthy else ""
            lines.append(f"{p} {proto}{lat}{err}")

        if st.consecutive_failures:
            lines.append(f"\n⚠️ Подряд неудач: {st.consecutive_failures}")
        if st.in_failover:
            lines.append("🔄 В failover")
        lines.append(f"\n<i>{_now_utc()}</i>")

        restart_row: list[tuple[str, str]] = []
        if node.is_exit:
            if "vless_reality" in node.protocols:
                restart_row.append(("🔄 xray", f"restart:{node.name}:xray"))
            if "hysteria2" in node.protocols:
                restart_row.append(("🔄 hy2", f"restart:{node.name}:hy2"))
            if "amneziawg" in node.protocols:
                restart_row.append(("🔄 awg", f"restart:{node.name}:awg"))
        elif node.is_bridge:
            restart_row.append(("🔄 xray", f"restart:{node.name}:xray"))

        kb_rows: list[list[tuple[str, str]]] = []
        if restart_row:
            kb_rows.append(restart_row)
        kb_rows.append([
            ("📋 Логи xray", f"logs:{node.name}"),
            ("💻 Ресурсы", f"sysinfo:{node.name}"),
        ])
        kb_rows.append([
            ("⚡ Статус сервисов", f"svc_status:{node.name}"),
            ("🔌 Reboot", f"reboot_confirm:{node.name}"),
        ])
        extra_row: list[tuple[str, str]] = [
            ("🧹 Очистка логов", f"cleanup_logs:{node.name}"),
            ("📡 Speed Test", f"speedtest:{node.name}"),
        ]
        if node.is_exit:
            extra_row.append(("🔑 AWG", f"awg_peers:{node.name}"))
        kb_rows.append(extra_row)
        kb_rows.append([
            ("📈 История", f"node_stats:{node.name}"),
            ("← Все ноды", "status"),
            ("🏠 Меню", "menu"),
        ])
        await self._edit(chat_id, msg_id, "\n".join(lines), _kb(kb_rows))

    # ------------------------------------------------------------------
    # Restart service
    # ------------------------------------------------------------------

    async def _do_restart(
        self, chat_id: int | str, msg_id: int, node_name: str, svc_key: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        svc = self._SERVICES.get(svc_key, svc_key)
        await self._edit(
            chat_id, msg_id,
            f"⏳ Перезапускаю <code>{svc}</code> на <code>{node.name}</code>...",
        )

        try:
            _, err, rc = await self._ssh(node, f"systemctl restart {svc}")
            if rc == 0:
                result = f"✅ <b>{svc}</b> перезапущен на <code>{node.name}</code>"
            else:
                result = f"❌ Ошибка (rc={rc})\n<code>{err[:400]}</code>"
        except Exception as e:
            result = f"❌ SSH: {e}"

        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    async def _show_logs(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Получаю логи <code>{node.name}</code>...")

        try:
            out, _, _ = await self._ssh(
                node, "journalctl -u xray --no-pager -n 30 --output=cat 2>/dev/null",
                timeout=15,
            )
            text = out.strip() or "(логи пусты)"
            if len(text) > 3400:
                text = "...\n" + text[-3300:]
        except Exception as e:
            text = f"SSH ошибка: {e}"

        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(
            chat_id, msg_id,
            f"<b>Логи xray @ {node.name}:</b>\n<code>{text}</code>",
            markup,
        )

    # ------------------------------------------------------------------
    # System info
    # ------------------------------------------------------------------

    async def _show_sysinfo(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Получаю ресурсы <code>{node.name}</code>...")

        cmd = (
            "top -bn1 2>/dev/null | grep -m1 '%Cpu' | "
            r"awk '{idle=$8; gsub(/,/,\".\",idle); printf \"CPU: %.1f%%\n\", 100-idle}'; "
            "free -h | awk '/^Mem/{print \"RAM: \" $3 \" / \" $2}'; "
            "df -h / | awk 'NR==2{print \"Disk: \" $3 \" / \" $2 \" (\" $5 \")\"}'; "
            "uptime -p 2>/dev/null || uptime"
        )
        try:
            out, _, _ = await self._ssh(node, cmd, timeout=15)
            text_body = out.strip() or "(нет данных)"
        except Exception as e:
            text_body = f"SSH ошибка: {e}"

        text = f"💻 <b>Ресурсы {node.name}</b>\n\n<code>{text_body}</code>\n\n<i>{_now_utc()}</i>"
        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # Service status
    # ------------------------------------------------------------------

    async def _show_svc_status(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Получаю статус сервисов <code>{node.name}</code>...")

        services: list[str] = []
        if node.is_exit:
            if "vless_reality" in node.protocols:
                services.append("xray")
            if "hysteria2" in node.protocols:
                services.append("hysteria2")
            if "amneziawg" in node.protocols:
                services.append("wg-quick@awg0")
        elif node.is_bridge:
            services.append("xray")

        if not services:
            await self._edit(chat_id, msg_id, "❌ Нет известных сервисов для этой ноды.")
            return

        parts = [
            f"echo '--- {s} ---' && systemctl status {s} --no-pager -n 5 2>&1 | head -10"
            for s in services
        ]
        cmd = " ; ".join(parts)

        try:
            out, _, _ = await self._ssh(node, cmd, timeout=20)
            text_body = out.strip() or "(нет данных)"
            if len(text_body) > 3200:
                text_body = text_body[-3200:]
        except Exception as e:
            text_body = f"SSH ошибка: {e}"

        text = (
            f"⚡ <b>Статус сервисов {node.name}</b>\n\n"
            f"<code>{text_body}</code>\n\n<i>{_now_utc()}</i>"
        )
        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # Reboot
    # ------------------------------------------------------------------

    async def _reboot_confirm(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        text = (
            f"⚠️ <b>Перезагрузить {node.name}?</b>\n\n"
            f"<code>{node.ip}</code>\n\n"
            "Все соединения через эту ноду будут прерваны на ~60 секунд."
        )
        markup = _kb([[
            ("✅ Да, перезагрузить", f"reboot_exec:{node.name}"),
            ("❌ Отмена", f"status_node:{node.name}"),
        ]])
        await self._edit(chat_id, msg_id, text, markup)

    async def _reboot_exec(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Отправляю reboot на <code>{node.name}</code>...")

        try:
            await self._ssh(node, "nohup sh -c 'sleep 1 && reboot' &>/dev/null &", timeout=10)
            result = f"✅ Команда reboot отправлена на <code>{node.name}</code>"
        except Exception as e:
            result = f"❌ SSH ошибка: {e}"

        markup = _kb([[("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Node stats (uptime / incidents from StatsDB)
    # ------------------------------------------------------------------

    async def _node_stats(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        if not self.monitor or not hasattr(self.monitor, "stats"):
            await self._edit(chat_id, msg_id, "❌ StatsDB не инициализирована.")
            return

        db = self.monitor.stats

        def _query():
            sources = ["local"]
            bridge_probes = [
                n.name for n in self.nm.bridge_nodes()
                if n.single_inbound_port
            ]
            sources += bridge_probes

            uptime: dict[str, dict] = {}
            for src in sources:
                uptime[src] = {
                    "24h": db.uptime_pct(node_name, hours=24, probe_src=src),
                    "7d":  db.uptime_pct(node_name, hours=168, probe_src=src),
                    "30d": db.uptime_pct(node_name, hours=720, probe_src=src),
                    "lat": db.avg_latency(node_name, hours=24, probe_src=src),
                }
            incidents = db.recent_incidents(node_name, limit=7)
            return uptime, incidents

        try:
            uptime, incidents = await self._run(_query)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка: {e}")
            return

        def _pct(v):
            return f"{v:.1f}%" if v is not None else "—"

        def _dur(s):
            if s is None:
                return "текущий"
            if s < 60:
                return f"{s}с"
            if s < 3600:
                return f"{s // 60}м"
            return f"{s // 3600}ч {(s % 3600) // 60}м"

        def _ts(t):
            if not t:
                return "?"
            return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%m-%d %H:%M")

        lines = [f"📈 <b>Статистика — {node.name}</b>\n"]

        src_labels = {"local": "local (mgmt bridge)", **{n: n for n in [
            b.name for b in self.nm.bridge_nodes() if b.single_inbound_port
        ]}}

        for src, label in src_labels.items():
            u = uptime.get(src, {})
            u24 = u.get("24h")
            if u24 is None:
                continue
            lat = f"  avg {u.get('lat')}ms" if u.get("lat") else ""
            lines.append(
                f"<b>{label}</b>\n"
                f"  24h: {_pct(u24)}  7d: {_pct(u.get('7d'))}  "
                f"30d: {_pct(u.get('30d'))}{lat}"
            )

        if incidents:
            lines.append("\n<b>Последние инциденты:</b>")
            for inc in incidents:
                src = inc["probe_src"]
                start = _ts(inc["started_at"])
                dur = _dur(inc["duration_s"])
                ended = "✓" if inc["ended_at"] else "🔴"
                lines.append(f"  {ended} {start}  {dur}  <i>{src}</i>")
        else:
            lines.append("\n<i>Инцидентов пока нет</i>")

        lines.append(f"\n<i>{_now_utc()}</i>")

        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, "\n".join(lines), markup)

    # ------------------------------------------------------------------
    # Log cleanup
    # ------------------------------------------------------------------

    async def _cleanup_logs_confirm(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Получаю размер журналов <code>{node.name}</code>...")

        try:
            out, _, _ = await self._ssh(node, "journalctl --disk-usage 2>/dev/null", timeout=10)
            usage = out.strip() or "неизвестно"
        except Exception as e:
            usage = f"ошибка SSH: {e}"

        text = (
            f"🧹 <b>Очистка логов — {node.name}</b>\n\n"
            f"Текущий размер: <code>{usage}</code>\n\n"
            "Будут удалены журналы старше 7 дней (макс. 100 MB на хранение)."
        )
        markup = _kb([[
            ("✅ Очистить", f"cleanup_exec:{node.name}"),
            ("❌ Отмена", f"status_node:{node.name}"),
        ]])
        await self._edit(chat_id, msg_id, text, markup)

    async def _cleanup_logs_exec(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Очищаю логи <code>{node.name}</code>...")

        cmd = (
            "journalctl --vacuum-time=7d --vacuum-size=100M 2>&1 | tail -5; "
            "echo '---'; "
            "journalctl --disk-usage 2>/dev/null; "
            "df -h / | awk 'NR==2{print $4 \" свободно на /\"}'"
        )
        try:
            out, _, _ = await self._ssh(node, cmd)
            body = out.strip() or "(нет вывода)"
        except Exception as e:
            body = f"SSH ошибка: {e}"

        text = f"🧹 <b>Очистка завершена — {node.name}</b>\n\n<code>{body}</code>"
        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)

    # ------------------------------------------------------------------
    # Speed test
    # ------------------------------------------------------------------

    async def _speedtest_node(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(
            chat_id, msg_id,
            f"⏳ Запускаю speed test на <code>{node.name}</code>...\n"
            "<i>Это займёт ~30 секунд</i>",
        )

        cmd = (
            "command -v speedtest-cli >/dev/null 2>&1 "
            "|| pip3 install speedtest-cli -q 2>/dev/null "
            "|| apt-get install -y -q speedtest-cli 2>/dev/null; "
            "speedtest-cli --json --secure 2>/dev/null"
        )
        try:
            out, _, _ = await self._ssh(node, cmd, timeout=120)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ SSH ошибка: {e}")
            return

        json_start = out.find("{")
        if json_start == -1:
            await self._edit(
                chat_id, msg_id,
                f"❌ speedtest-cli не вернул JSON.\n<code>{out[-500:]}</code>",
            )
            return

        try:
            data = json.loads(out[json_start:])
            dl = data["download"] / 1_000_000
            ul = data["upload"] / 1_000_000
            ping = data["ping"]
            srv = data.get("server", {})
            server_info = f"{srv.get('sponsor', '?')}, {srv.get('country', '?')}"
        except (json.JSONDecodeError, KeyError) as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка парсинга: {e}")
            return

        text = (
            f"📡 <b>Speed Test — {node.name}</b>\n\n"
            f"↓ Download:  <b>{dl:.1f} Mbit/s</b>\n"
            f"↑ Upload:    <b>{ul:.1f} Mbit/s</b>\n"
            f"⏱ Ping:      <b>{ping:.1f} ms</b>\n"
            f"🖥 Сервер:   {server_info}\n\n"
            f"<i>{_now_utc()}</i>"
        )
        markup = _kb([[(f"← {node.name}", f"status_node:{node.name}"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, text, markup)
