"""Background loops: digest, traffic alerts, portal monitor."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from core.bot._helpers import _fmt_bytes, _now_utc, _parse_gb


class LoopsMixin:
    """Async background loops running alongside the bot."""

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------

    async def _digest_loop(self) -> None:
        digest_time: str = self.cfg.get("telegram", {}).get("digest_time", "09:00")
        try:
            dh, dm = map(int, digest_time.split(":"))
        except Exception:
            dh, dm = 9, 0
        last_date = None
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)
            if now.hour == dh and now.minute == dm:
                today = now.date()
                if last_date != today:
                    last_date = today
                    try:
                        await self._send_digest()
                    except Exception as e:
                        print(f"[bot] digest error: {e}")

    async def _send_digest(self) -> None:
        if self.monitor:
            cached = self.monitor.all_statuses()
            if not cached:
                nodes = self.nm.enabled_nodes()
                results = await asyncio.gather(
                    *[self.monitor.run_checks(n) for n in nodes],
                    return_exceptions=True,
                )
                cached = {
                    n.name: r
                    for n, r in zip(nodes, results)
                    if not isinstance(r, Exception)
                }
        else:
            cached = {}

        lines: list[str] = [
            f"📋 <b>Ежедневный дайджест</b>  "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        ]
        lines.append("\n<b>Статус нод:</b>")
        for node in self.nm.all_nodes():
            st = cached.get(node.name)
            h = "🟢" if (st and st.overall_healthy) else ("🔴" if st else "⚪")
            icmp = f"  {st.icmp_latency_ms:.0f}ms" if (st and st.icmp_latency_ms) else ""
            lines.append(f"  {h} {node.name}{icmp}")

        exit_nodes = self.nm.exit_nodes()
        traffic_results = await asyncio.gather(
            *[self._run(self._fetch_traffic_ssh, n) for n in exit_nodes],
            return_exceptions=True,
        )
        lines.append("\n<b>Трафик за месяц:</b>")
        for node, tr in zip(exit_nodes, traffic_results):
            if isinstance(tr, Exception) or (isinstance(tr, dict) and tr.get("error") and not tr.get("month")):
                lines.append(f"  {node.name}: N/A")
            elif isinstance(tr, dict) and tr.get("month"):
                rx, tx = tr["month"]
                lines.append(f"  {node.name}: ↓{rx}  ↑{tx}")
            else:
                lines.append(f"  {node.name}: N/A")

        await self._send(self.chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Traffic threshold alerts
    # ------------------------------------------------------------------

    async def _traffic_alert_loop(self) -> None:
        threshold_gb = float(
            self.cfg.get("monitoring", {}).get("traffic_alert_gb", 0)
        )
        if threshold_gb <= 0:
            return

        alerted: set[str] = set()
        last_month: int = -1

        while True:
            await asyncio.sleep(3600)
            now = datetime.now(timezone.utc)
            if now.month != last_month:
                alerted.clear()
                last_month = now.month

            exit_nodes = self.nm.exit_nodes()
            results = await asyncio.gather(
                *[self._run(self._fetch_traffic_ssh, n) for n in exit_nodes],
                return_exceptions=True,
            )
            for node, tr in zip(exit_nodes, results):
                if isinstance(tr, Exception) or not isinstance(tr, dict):
                    continue
                if node.name in alerted:
                    continue
                if tr.get("month"):
                    rx_str, tx_str = tr["month"]
                    total_gb = _parse_gb(rx_str) + _parse_gb(tx_str)
                    if total_gb >= threshold_gb:
                        alerted.add(node.name)
                        try:
                            await self._send(
                                self.chat_id,
                                f"⚠️ <b>Трафик {node.name} достиг {total_gb:.1f} GB</b>\n"
                                f"Порог: {threshold_gb:.0f} GB\n"
                                f"Месяц: ↓{rx_str}  ↑{tx_str}",
                            )
                        except Exception as e:
                            print(f"[bot] traffic alert send: {e}")

    # ------------------------------------------------------------------
    # Portal availability monitor
    # ------------------------------------------------------------------

    async def _portal_monitor_loop(self) -> None:
        interval = int(
            self.cfg.get("telegram", {}).get("portal_check_interval", 300)
        )
        sub_uuid = os.environ.get("SUBSCRIPTION_UUID", "")
        sub_cfg = self.cfg.get("subscription", {})
        base_url = sub_cfg.get("base_url", "").rstrip("/")
        url = f"{base_url}/{sub_uuid}" if sub_uuid else base_url
        if not url:
            return

        while True:
            await asyncio.sleep(interval)
            try:
                session = await self._get_http_session()
                async with session.get(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    is_down = r.status != 200
            except Exception:
                is_down = True

            if is_down and not self._portal_was_down:
                self._portal_was_down = True
                try:
                    await self._send(
                        self.chat_id,
                        "🔴 <b>Портал недоступен!</b>\n"
                        f"<code>{url}</code>",
                    )
                except Exception:
                    pass
            elif not is_down and self._portal_was_down:
                self._portal_was_down = False
                try:
                    await self._send(
                        self.chat_id,
                        f"🟢 <b>Портал восстановлен</b>  {_now_utc()}",
                    )
                except Exception:
                    pass
