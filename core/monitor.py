"""
Async node monitor — health-checks all nodes periodically and triggers
failover via BridgeManager when consecutive failure threshold is exceeded.
"""

from __future__ import annotations

import asyncio
import os
import platform
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

import yaml

if TYPE_CHECKING:
    from core.bridge_manager import BridgeManager
    from core.node_manager import Node, NodeManager
    from core.telegram import TelegramNotifier

from core.stats import StatsDB


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProtocolStatus:
    name: str
    healthy: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    checked_at: Optional[datetime] = None


@dataclass
class NodeStatus:
    node_name: str
    ip: str
    overall_healthy: bool
    icmp_ok: bool
    icmp_latency_ms: Optional[float]
    protocol_statuses: dict[str, ProtocolStatus] = field(default_factory=dict)
    consecutive_failures: int = 0
    last_check_time: Optional[datetime] = None
    in_failover: bool = False

    def update_from_check(self, healthy: bool) -> None:
        self.last_check_time = datetime.utcnow()
        if healthy:
            self.consecutive_failures = 0
            self.overall_healthy = True
        else:
            self.consecutive_failures += 1
            self.overall_healthy = False


# ---------------------------------------------------------------------------
# NodeMonitor
# ---------------------------------------------------------------------------

class NodeMonitor:
    """Polls nodes and drives failover logic."""

    def __init__(
        self,
        node_manager: "NodeManager",
        bridge_manager: "BridgeManager",
        global_config: dict,
        notifier: Optional["TelegramNotifier"] = None,
    ) -> None:
        self.nm = node_manager
        self.bm = bridge_manager
        self.cfg = global_config
        self.notifier = notifier

        self.monitor_interval: int = int(
            self.cfg.get("monitoring", {}).get("interval", 30)
        )
        self.failover_threshold: int = int(
            self.cfg.get("monitoring", {}).get("failover_threshold", 3)
        )
        self._statuses: dict[str, NodeStatus] = {}
        self._running = False

        # SQLite статистика
        db_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "stats.db"
        )
        self.stats = StatsDB(os.path.normpath(db_path))

        # Счётчики отказов при bridge-зондировании: "{bridge}:{node}" → int
        self._bridge_probe_failures: dict[str, int] = {}

        # message_id алертов NODE DOWN: node_name → message_id
        # При восстановлении сообщение удаляется вместо нового "recovered"
        self._down_alert_msg_ids: dict[str, int] = {}
        # То же для bridge-probe алертов: "{bridge}:{node}" → message_id
        self._bridge_alert_msg_ids: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Low-level probes
    # ------------------------------------------------------------------

    @staticmethod
    async def check_icmp(ip: str) -> tuple[bool, Optional[float]]:
        """
        Ping *ip* once; returns (reachable, latency_ms).
        Works on both Windows and Linux.
        """
        param = "-n" if platform.system().lower() == "windows" else "-c"
        timeout_flag = "-w" if platform.system().lower() == "windows" else "-W"
        cmd = ["ping", param, "1", timeout_flag, "3", ip]
        try:
            loop = asyncio.get_event_loop()
            t0 = loop.time()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=8)
            latency = (loop.time() - t0) * 1000
            if proc.returncode == 0:
                return True, round(latency, 2)
            return False, None
        except (asyncio.TimeoutError, Exception):
            return False, None

    @staticmethod
    async def check_tcp_port(ip: str, port: int, timeout: float = 5.0) -> bool:
        """Return True if a TCP connection to ip:port succeeds within *timeout* s."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
            return False

    # ------------------------------------------------------------------
    # Protocol-level health checks
    # ------------------------------------------------------------------

    async def check_xray_health(self, node: "Node") -> ProtocolStatus:
        """Check VLESS/Reality by probing the TCP port."""
        ports = self.cfg.get("ports", {})
        port = int(ports.get("vless_reality", 443))
        t0 = time.monotonic()
        ok = await self.check_tcp_port(node.ip, port, timeout=5)
        latency = round((time.monotonic() - t0) * 1000, 2)
        return ProtocolStatus(
            name="vless_reality",
            healthy=ok,
            latency_ms=latency if ok else None,
            error=None if ok else f"TCP port {port} unreachable",
            checked_at=datetime.utcnow(),
        )

    async def check_hysteria2_health(self, node: "Node") -> ProtocolStatus:
        """
        Check Hysteria2 via UDP probe.
        Sends a garbage UDP packet: если сервис запущен — ядро не вернёт ICMP
        port-unreachable, если не запущен — вернёт ConnectionRefusedError.
        """
        ports = self.cfg.get("ports", {})
        port = int(ports.get("hysteria2", 8443))

        loop = asyncio.get_event_loop()
        t0 = time.monotonic()

        def _udp_probe() -> bool:
            import socket as _socket
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            sock.settimeout(3.0)
            try:
                sock.sendto(b"\x00" * 16, (node.ip, port))
                sock.recvfrom(64)  # hy2 может ответить QUIC-отказом
                return True
            except _socket.timeout:
                return True   # нет ICMP unreachable → сервис слушает
            except ConnectionRefusedError:
                return False  # ICMP port unreachable → сервис не запущен
            except Exception:
                return True   # неизвестная ошибка — не считаем упавшим
            finally:
                sock.close()

        ok = await loop.run_in_executor(None, _udp_probe)
        latency = round((time.monotonic() - t0) * 1000, 2)
        return ProtocolStatus(
            name="hysteria2",
            healthy=ok,
            latency_ms=latency if ok else None,
            error=None if ok else f"UDP port {port} unreachable (ICMP port-unreachable)",
            checked_at=datetime.utcnow(),
        )

    async def check_amneziawg_health(self, node: "Node") -> ProtocolStatus:
        """Check AmneziaWG by probing the UDP port (via ICMP as a secondary signal)."""
        ports = self.cfg.get("ports", {})
        port = int(ports.get("amneziawg", 51820))
        # WireGuard/AmneziaWG is UDP — we can't do a true TCP check.
        # We use ICMP reachability as a proxy for service health.
        icmp_ok, latency = await self.check_icmp(node.ip)
        return ProtocolStatus(
            name="amneziawg",
            healthy=icmp_ok,
            latency_ms=latency,
            error=None if icmp_ok else f"ICMP unreachable (UDP port {port} not directly testable)",
            checked_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Full node check
    # ------------------------------------------------------------------

    async def run_checks(self, node: "Node") -> NodeStatus:
        """Run all health checks for a single node and return a NodeStatus."""
        icmp_ok, icmp_latency = await self.check_icmp(node.ip)

        protocol_statuses: dict[str, ProtocolStatus] = {}

        if node.is_exit:
            # Run protocol checks concurrently
            tasks = []
            if "vless_reality" in node.protocols:
                tasks.append(("vless_reality", self.check_xray_health(node)))
            if "hysteria2" in node.protocols:
                tasks.append(("hysteria2", self.check_hysteria2_health(node)))
            if "amneziawg" in node.protocols:
                tasks.append(("amneziawg", self.check_amneziawg_health(node)))

            if tasks:
                results = await asyncio.gather(
                    *(coro for _, coro in tasks), return_exceptions=True
                )
                for (proto_name, _), result in zip(tasks, results):
                    if isinstance(result, Exception):
                        protocol_statuses[proto_name] = ProtocolStatus(
                            name=proto_name,
                            healthy=False,
                            error=str(result),
                            checked_at=datetime.utcnow(),
                        )
                    else:
                        protocol_statuses[proto_name] = result

        elif node.is_bridge:
            # Single-port bridges listen on single_inbound_port (443).
            # Multi-port bridges use inbound_port_start.
            if node.single_inbound_port:
                check_port = node.single_inbound_port
            else:
                bridge_cfg = self.cfg.get("bridge", {})
                check_port = int(bridge_cfg.get("inbound_port_start", 0))
            ok = await self.check_tcp_port(node.ip, check_port, timeout=5)
            protocol_statuses["bridge_inbound"] = ProtocolStatus(
                name="bridge_inbound",
                healthy=ok,
                latency_ms=None,
                error=None if ok else f"Bridge inbound port {check_port} unreachable",
                checked_at=datetime.utcnow(),
            )

        # Overall health: ICMP must be up, and at least one protocol healthy
        if node.is_exit and protocol_statuses:
            any_proto_up = any(ps.healthy for ps in protocol_statuses.values())
            overall = icmp_ok and any_proto_up
        else:
            overall = icmp_ok

        # Merge with existing status (to preserve consecutive_failures)
        existing = self._statuses.get(node.name)
        if existing is None:
            status = NodeStatus(
                node_name=node.name,
                ip=node.ip,
                overall_healthy=overall,
                icmp_ok=icmp_ok,
                icmp_latency_ms=icmp_latency,
                protocol_statuses=protocol_statuses,
            )
        else:
            status = existing
            status.ip = node.ip
            status.icmp_ok = icmp_ok
            status.icmp_latency_ms = icmp_latency
            status.protocol_statuses = protocol_statuses

        status.update_from_check(overall)
        self._statuses[node.name] = status
        return status

    # ------------------------------------------------------------------
    # Failover logic
    # ------------------------------------------------------------------

    async def _handle_failover(self, failed_node: "Node") -> None:
        """Find a backup node and trigger failover on the bridge."""
        from core.node_manager import NodeManager  # avoid circular at module level

        exit_nodes = self.nm.exit_nodes()
        # Find the best available backup (different name, healthy, lowest priority number)
        candidates = [
            n for n in exit_nodes
            if n.name != failed_node.name
            and not self._statuses.get(n.name, NodeStatus(
                node_name=n.name, ip=n.ip, overall_healthy=True,
                icmp_ok=True, icmp_latency_ms=None
            )).in_failover
        ]
        candidates.sort(key=lambda n: (n.priority, n.name))

        if not candidates:
            if self.notifier:
                await self.notifier.send_message(
                    f"CRITICAL: Node {failed_node.name} is down and NO backup nodes available!"
                )
            return

        backup_node = candidates[0]

        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self.bm.failover, failed_node, backup_node
            )
            if self._statuses.get(failed_node.name):
                self._statuses[failed_node.name].in_failover = True

            if self.notifier:
                await self.notifier.notify_failover(failed_node, backup_node)
        except Exception as exc:
            if self.notifier:
                await self.notifier.send_message(
                    f"ERROR: Failover from {failed_node.name} to {backup_node.name} failed: {exc}"
                )

    async def _handle_recovery(self, node: "Node") -> None:
        """Restore a recovered node to rotation."""
        status = self._statuses.get(node.name)
        if status and status.in_failover:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.bm.failback, node
                )
                status.in_failover = False
                if self.notifier:
                    await self.notifier.notify_node_recovered(node)
            except Exception as exc:
                if self.notifier:
                    await self.notifier.send_message(
                        f"ERROR: Failback for {node.name} failed: {exc}"
                    )

    # ------------------------------------------------------------------
    # Bridge probe loop (зондирование с single-port bridge-нод)
    # ------------------------------------------------------------------

    async def bridge_probe_loop(self) -> None:
        """
        Периодически SSH-ует на bridge-ноды с single_inbound_port
        и проверяет TCP-доступность exit-нод оттуда.
        """
        while self._running:
            await asyncio.sleep(self.monitor_interval)
            probe_bridges = [
                n for n in self.nm.bridge_nodes()
                if n.single_inbound_port  # single-port bridges only
            ]
            exit_nodes = self.nm.exit_nodes()
            if not probe_bridges or not exit_nodes:
                continue

            loop = asyncio.get_event_loop()
            await asyncio.gather(
                *(
                    loop.run_in_executor(
                        None, self._run_bridge_probe, bridge, exit_nodes,
                    )
                    for bridge in probe_bridges
                ),
                return_exceptions=True,
            )

    def _run_bridge_probe(
        self, bridge: "Node", exit_nodes: list["Node"]
    ) -> None:
        """Блокирующий: SSH на bridge, TCP-пинг всех exit-нод, запись в stats."""
        ports_cfg = self.cfg.get("ports", {})
        vless_port = int(ports_cfg.get("vless_reality", 443))

        targets = [(n.name, n.ip, vless_port) for n in exit_nodes]
        script = (
            "import socket, time\n"
            f"targets = {targets!r}\n"
            "for name, ip, port in targets:\n"
            "    t0 = time.time()\n"
            "    try:\n"
            "        s = socket.create_connection((ip, port), timeout=4)\n"
            "        s.close()\n"
            "        print(name, 'ok', int((time.time()-t0)*1000))\n"
            "    except Exception as e:\n"
            "        print(name, 'fail', 0)\n"
        )

        with self.nm.ssh(bridge) as conn:
            conn.upload_content(script, "/tmp/_bw_bp.py")
            out, _, _ = conn.exec(
                "python3 /tmp/_bw_bp.py; rm -f /tmp/_bw_bp.py", timeout=25
            )

        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            node_name, result = parts[0], parts[1]
            latency = float(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            healthy = result == "ok"

            self.stats.record_check(
                node_name=node_name,
                healthy=healthy,
                latency_ms=latency,
                probe_src=bridge.name,
            )

            key = f"{bridge.name}:{node_name}"
            prev = self._bridge_probe_failures.get(key, 0)

            if not healthy:
                self._bridge_probe_failures[key] = prev + 1
                # Алерт при первом отказе — сохраняем message_id
                if prev == 0 and self.notifier:
                    node_obj = next(
                        (n for n in exit_nodes if n.name == node_name), None
                    )
                    if node_obj:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.notifier.send_message(
                                f"⚠️ <b>{bridge.name} → {node_name}</b> недоступен\n"
                                f"TCP {node_obj.ip}:{vless_port} timeout"
                            ),
                            asyncio.get_event_loop(),
                        )
                        print(f"[monitor] bridge_probe FAIL: {bridge.name} → {node_name}")
                        try:
                            msg_id = fut.result(timeout=10)
                            if msg_id:
                                self._bridge_alert_msg_ids[key] = msg_id
                        except Exception:
                            pass
            else:
                if prev > 0:
                    # Восстановление — удаляем алерт вместо нового сообщения
                    self._bridge_probe_failures[key] = 0
                    msg_id = self._bridge_alert_msg_ids.pop(key, None)
                    if msg_id and self.notifier:
                        asyncio.run_coroutine_threadsafe(
                            self.notifier.delete_message(msg_id),
                            asyncio.get_event_loop(),
                        )
                    print(f"[monitor] bridge_probe RECOVERED: {bridge.name} → {node_name}")

    # ------------------------------------------------------------------
    # Monitor loop
    # ------------------------------------------------------------------

    async def monitor_loop(self) -> None:
        """
        Main async loop. Checks all enabled nodes every monitor_interval
        seconds and drives failover/failback.
        """
        self._running = True
        print(
            f"[monitor] Starting — interval={self.monitor_interval}s "
            f"failover_threshold={self.failover_threshold}"
        )
        asyncio.ensure_future(self.bridge_probe_loop())
        while self._running:
            nodes = self.nm.enabled_nodes()

            # Запоминаем количество сбоев ДО проверки, чтобы определить момент восстановления
            prev_failures = {
                n.name: self._statuses[n.name].consecutive_failures
                for n in nodes
                if n.name in self._statuses
            }

            check_coros = [self.run_checks(n) for n in nodes]
            results = await asyncio.gather(*check_coros, return_exceptions=True)

            for node, result in zip(nodes, results):
                if isinstance(result, Exception):
                    print(f"[monitor] ERROR checking {node.name}: {result}")
                    continue

                status: NodeStatus = result

                # Пишем в статистику
                self.stats.record_check(
                    node_name=node.name,
                    healthy=status.overall_healthy,
                    icmp_ok=status.icmp_ok,
                    latency_ms=status.icmp_latency_ms,
                    probe_src="local",
                )

                if not status.overall_healthy:
                    print(
                        f"[monitor] {node.name} UNHEALTHY "
                        f"(consecutive failures: {status.consecutive_failures})"
                    )
                    if (
                        status.consecutive_failures == 1
                        and self.notifier
                        and node.is_exit
                    ):
                        msg_id = await self.notifier.notify_node_down(
                            node, reason="Health check failed"
                        )
                        if msg_id:
                            self._down_alert_msg_ids[node.name] = msg_id
                    # Trigger failover once threshold is hit (only once)
                    if (
                        status.consecutive_failures == self.failover_threshold
                        and node.is_exit
                        and not status.in_failover
                    ):
                        print(
                            f"[monitor] Triggering failover for {node.name} "
                            f"after {status.consecutive_failures} failures"
                        )
                        await self._handle_failover(node)
                else:
                    if status.in_failover:
                        print(f"[monitor] {node.name} RECOVERED — restoring to rotation")
                        await self._handle_recovery(node)
                    elif prev_failures.get(node.name, 0) > 0 and node.is_exit:
                        # Нода восстановилась до порога failover — удаляем алерт вместо нового сообщения
                        print(f"[monitor] {node.name} RECOVERED (before failover threshold)")
                        msg_id = self._down_alert_msg_ids.pop(node.name, None)
                        if msg_id and self.notifier:
                            await self.notifier.delete_message(msg_id)

            await asyncio.sleep(self.monitor_interval)

    def stop(self) -> None:
        self._running = False

    def get_status(self, node_name: str) -> Optional[NodeStatus]:
        return self._statuses.get(node_name)

    def all_statuses(self) -> dict[str, NodeStatus]:
        return dict(self._statuses)
