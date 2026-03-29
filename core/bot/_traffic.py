"""Traffic collection (SSH / vnstat / /proc/net/dev)."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from core.bot._helpers import _flag, _fmt_bytes, _kb, _now_utc


class TrafficMixin:
    """Traffic display and SSH-based collection."""

    async def _traffic_all(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        msg_id = await self._show(chat_id, msg_id, "⏳ Собираю трафик (SSH)...")

        nodes = self.nm.enabled_nodes()
        results = await asyncio.gather(
            *[self._run(self._fetch_traffic_ssh, n) for n in nodes],
            return_exceptions=True,
        )

        lines = ["<b>Трафик по нодам:</b>"]
        for node, result in zip(nodes, results):
            lines.append(f"\n📊 <b>{node.name}</b> {_flag(node.region)}")
            if isinstance(result, Exception):
                lines.append(f"   ❌ {result}")
                continue
            if result.get("error") and not result.get("today"):
                lines.append(f"   ⚠️ {result['error']}")
                continue
            if result.get("today"):
                rx, tx = result["today"]
                lines.append(f"   Сегодня  ↓{rx}  ↑{tx}")
            if result.get("month"):
                rx, tx = result["month"]
                lines.append(f"   Месяц    ↓{rx}  ↑{tx}")
            if result.get("iface"):
                lines.append(f"   <i>{result['iface']}</i>")
            if result.get("error"):
                lines.append(f"   <i>⚠️ {result['error']}</i>")

        lines.append(f"\n<i>{_now_utc()}</i>")
        markup = _kb([
            [("📅 За месяц", "traffic_month"), ("🔄 Обновить", "traffic"), ("🏠 Меню", "menu")],
        ])
        await self._edit(chat_id, msg_id, "\n".join(lines), markup)

    # ------------------------------------------------------------------
    # Monthly traffic breakdown
    # ------------------------------------------------------------------

    async def _traffic_month(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        msg_id = await self._show(chat_id, msg_id, "⏳ Собираю помесячный трафик...")

        nodes = self.nm.enabled_nodes()
        results = await asyncio.gather(
            *[self._run(self._fetch_monthly_ssh, n) for n in nodes],
            return_exceptions=True,
        )

        lines = ["<b>Трафик по месяцам:</b>"]
        for node, result in zip(nodes, results):
            lines.append(f"\n📊 <b>{node.name}</b> {_flag(node.region)}")
            if isinstance(result, Exception):
                lines.append(f"   ❌ {result}")
                continue
            if result.get("error"):
                lines.append(f"   ⚠️ {result['error']}")
                continue
            for entry in result.get("months", []):
                lines.append(f"   {entry['label']}  ↓{entry['rx']}  ↑{entry['tx']}")
            if not result.get("months"):
                lines.append("   нет данных")

        lines.append(f"\n<i>{_now_utc()}</i>")
        markup = _kb([
            [("📊 Сегодня/месяц", "traffic"), ("🏠 Меню", "menu")],
        ])
        await self._edit(chat_id, msg_id, "\n".join(lines), markup)

    def _fetch_monthly_ssh(self, node) -> dict:
        """Get monthly traffic breakdown via vnstat --json m."""
        try:
            iface_out, _, _ = self.nm.exec_command(
                node, "ip route | awk '/default/{print $5; exit}'", timeout=10,
            )
            iface = iface_out.strip() or "eth0"
        except Exception as e:
            return {"error": str(e)}

        try:
            out, _, _ = self.nm.exec_command(
                node, f"vnstat -i {iface} --json m 2>/dev/null", timeout=15,
            )
        except Exception as e:
            return {"error": str(e)}

        if not out.strip():
            return {"error": "vnstat не установлен или нет данных"}

        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            return {"error": f"vnstat JSON: {e}"}

        try:
            ifaces = data.get("interfaces", [])
            if not ifaces:
                return {"error": "vnstat: нет интерфейсов"}
            ifc = max(
                ifaces,
                key=lambda i: i.get("traffic", {}).get("total", {}).get("rx", 0),
            )
            months_data = ifc.get("traffic", {}).get("month", [])
            months = []
            for m in months_data[-6:]:
                dt = m.get("date", {})
                label = f"{dt.get('year', '?')}-{dt.get('month', '?'):02d}"
                months.append({
                    "label": label,
                    "rx": _fmt_bytes(m.get("rx", 0)),
                    "tx": _fmt_bytes(m.get("tx", 0)),
                })
            return {"months": months}
        except (KeyError, TypeError) as e:
            return {"error": f"vnstat parse: {e}"}

    # ------------------------------------------------------------------
    # SSH-based traffic collection (blocking, for executor)
    # ------------------------------------------------------------------

    def _fetch_traffic_ssh(self, node) -> dict:
        """Get traffic via vnstat on the primary network interface."""
        try:
            iface_out, _, _ = self.nm.exec_command(
                node, "ip route | awk '/default/{print $5; exit}'", timeout=10,
            )
            iface = iface_out.strip() or "eth0"
        except Exception as e:
            return {"error": str(e)}

        try:
            install_out, _, _ = self.nm.exec_command(
                node,
                "command -v vnstat >/dev/null 2>&1 && echo HAS || "
                "(DEBIAN_FRONTEND=noninteractive apt-get install -y vnstat -q >/dev/null 2>&1 "
                "&& systemctl enable vnstat >/dev/null 2>&1 "
                "&& systemctl start vnstat >/dev/null 2>&1 "
                "&& echo INSTALLED || echo FAILED)",
                timeout=90,
            )
        except Exception as e:
            return {"error": str(e)}

        install_out = install_out.strip()

        if "FAILED" in install_out:
            return {"error": "не удалось установить vnstat"}

        if "INSTALLED" in install_out:
            return {"error": f"vnstat установлен ✓ (интерфейс: {iface}) — данные через ~5 мин"}

        try:
            data_out, _, _ = self.nm.exec_command(
                node, f"vnstat -i {iface} --json 2>/dev/null", timeout=15,
            )
        except Exception as e:
            return {"error": str(e)}

        if not data_out.strip():
            return self._traffic_from_proc(node, iface)

        result = self._parse_vnstat_json(data_out)

        if not result.get("today") and not result.get("month"):
            proc = self._traffic_from_proc(node, iface)
            if proc.get("today"):
                proc["error"] = "vnstat собирает данные — пока показываем счётчик с перезагрузки"
                return proc

        return result

    def _traffic_from_proc(self, node, iface: str = "") -> dict:
        """Fallback: raw counters from /proc/net/dev."""
        script = (
            "target = " + repr(iface) + "\n"
            "skip = {'lo', 'awg0', 'wg0', 'tun0', 'docker0'}\n"
            "for line in open('/proc/net/dev'):\n"
            "    s = line.strip()\n"
            "    if ':' not in s: continue\n"
            "    name, _, rest = s.partition(':')\n"
            "    name = name.strip()\n"
            "    if target and name != target: continue\n"
            "    if not target and name in skip: continue\n"
            "    fields = rest.split()\n"
            "    if len(fields) >= 9:\n"
            "        print(name, fields[0], fields[8])\n"
            "        break\n"
        )
        try:
            with self.nm.ssh(node) as conn:
                conn.upload_content(script, "/tmp/_bw_pnd.py")
                out, _, _ = conn.exec(
                    "python3 /tmp/_bw_pnd.py; rm -f /tmp/_bw_pnd.py", timeout=10,
                )
        except Exception as e:
            return {"error": str(e)}

        parts = out.strip().split()
        if len(parts) < 3:
            return {"error": "нет данных /proc/net/dev"}
        try:
            return {
                "today": (_fmt_bytes(int(parts[1])), _fmt_bytes(int(parts[2]))),
                "iface": f"{parts[0]} (с перезагрузки, без истории)",
            }
        except ValueError:
            return {"error": "не удалось разобрать /proc/net/dev"}

    @staticmethod
    def _parse_vnstat_json(raw: str) -> dict:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return {"error": f"vnstat JSON: {e}"}
        try:
            ifaces = data.get("interfaces", [])
            if not ifaces:
                return {"error": "vnstat: нет интерфейсов"}
            iface = max(
                ifaces,
                key=lambda i: i.get("traffic", {}).get("total", {}).get("rx", 0),
            )
            traffic = iface.get("traffic", {})
            result: dict = {"iface": iface.get("name", "?")}
            days = traffic.get("day", [])
            if days:
                d = days[-1]
                result["today"] = (_fmt_bytes(d.get("rx", 0)), _fmt_bytes(d.get("tx", 0)))
            months = traffic.get("month", [])
            if months:
                m = months[-1]
                result["month"] = (_fmt_bytes(m.get("rx", 0)), _fmt_bytes(m.get("tx", 0)))
            return result
        except (KeyError, TypeError) as e:
            return {"error": f"vnstat parse: {e}"}
