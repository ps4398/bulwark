"""
Bridge Manager — manages bridge xray configs.
Handles generating/uploading the bridge config and driving failover.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from core.config_gen import ConfigGenerator
from core.node_manager import NodeManager

if TYPE_CHECKING:
    from core.node_manager import Node, SSHConnection


class BridgeManager:
    """Controls the bridge xray configuration."""

    def __init__(
        self,
        node_manager: NodeManager,
        config_gen: Optional[ConfigGenerator] = None,
    ) -> None:
        self.nm = node_manager
        self.cg = config_gen or ConfigGenerator()
        self._override_disabled: set[str] = set()  # nodes disabled by failover

        # Load configurable paths from global.yaml
        _global = self.cg._load_global()
        _bridge_cfg = _global.get("bridge", {})
        _xray_cfg = _global.get("xray", {})
        self._bridge_config_path: str = _bridge_cfg.get(
            "xray_config_path",
            _xray_cfg.get("config_path", "/usr/local/etc/xray/config.json"),
        )
        self._xray_binary: str = _xray_cfg.get("binary", "/usr/local/bin/xray")

    # ------------------------------------------------------------------
    # Bridge config
    # ------------------------------------------------------------------

    def load_bridge_config(self) -> dict:
        """
        Download and parse the current xray config from the bridge.
        Returns parsed JSON dict, or empty dict on error.
        """
        bridge_nodes = self.nm.bridge_nodes()
        if not bridge_nodes:
            raise RuntimeError("No bridge nodes found in nodes.yaml")
        bridge = bridge_nodes[0]

        try:
            with self.nm.ssh(bridge) as conn:
                out, err, rc = conn.exec(f"cat {self._bridge_config_path}")
                if rc != 0:
                    raise RuntimeError(
                        f"Could not read bridge config (exit {rc}): {err}"
                    )
                return json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Bridge config is not valid JSON: {exc}") from exc

    def update_bridge(self, exit_nodes: Optional[list["Node"]] = None) -> None:
        """
        Regenerate xray_bridge.json from template using currently active exit nodes
        and upload it to the bridge node, then reload xray.
        """
        if exit_nodes is None:
            exit_nodes = self.nm.exit_nodes()

        # Filter out nodes that are temporarily disabled by failover
        active_nodes = [
            n for n in exit_nodes
            if n.name not in self._override_disabled
        ]

        if not active_nodes:
            raise RuntimeError(
                "Cannot update bridge: no active exit nodes available."
            )

        bridge_nodes = self.nm.bridge_nodes()
        if not bridge_nodes:
            raise RuntimeError("No bridge nodes found in nodes.yaml")

        # Count AWG-enabled exit nodes for firewall port opening
        awg_exit_count = sum(
            1 for n in active_nodes
            if n.enabled and n.is_exit and "amneziawg" in n.protocols
        )

        cfg_path = self._bridge_config_path
        errors: list[str] = []
        for bridge in bridge_nodes:
            config_content = self.cg.generate_xray_bridge(active_nodes, bridge_node=bridge)
            print(f"[bridge] Uploading new bridge config to {bridge.name} ({bridge.ip})...")
            try:
                with self.nm.ssh(bridge) as conn:
                    conn.exec(f"cp {cfg_path} {cfg_path}.bak 2>/dev/null || true")
                    conn.upload_content(config_content, cfg_path)
                    out, err, rc = conn.exec(f"{self._xray_binary} -test -config {cfg_path}")
                    if rc != 0:
                        conn.exec(f"cp {cfg_path}.bak {cfg_path} 2>/dev/null || true")
                        raise RuntimeError(
                            f"Bridge config validation failed — restored backup.\n{err}"
                        )
                    _, err, rc = conn.exec("systemctl restart xray")
                    if rc != 0:
                        raise RuntimeError(f"Failed to restart xray on bridge: {err}")

                    # Open UDP ports for AWG relay (dokodemo-door)
                    if awg_exit_count > 0:
                        self._ensure_awg_relay_firewall(
                            conn, bridge, awg_exit_count
                        )

                print(f"[bridge] {bridge.name}: config updated.")
            except Exception as exc:
                errors.append(f"{bridge.name}: {exc}")

        if errors:
            raise RuntimeError("Bridge update failed on some nodes:\n" + "\n".join(errors))

        print(f"[bridge] All bridges updated. Active nodes: {[n.name for n in active_nodes]}")

    # ------------------------------------------------------------------
    # Failover / failback
    # ------------------------------------------------------------------

    def failover(self, failed_node: "Node", backup_node: "Node") -> None:
        """
        Remove *failed_node* from bridge routing and ensure *backup_node* is active.
        Regenerates and uploads the bridge config.
        """
        print(
            f"[bridge] FAILOVER: removing {failed_node.name}, "
            f"ensuring {backup_node.name} is active."
        )
        self._override_disabled.add(failed_node.name)

        # Make sure backup is not in the disabled set
        self._override_disabled.discard(backup_node.name)

        self.update_bridge()

    def failback(self, node: "Node") -> None:
        """
        Restore *node* to bridge rotation after it has recovered.
        Regenerates and uploads the bridge config.
        """
        print(f"[bridge] FAILBACK: restoring {node.name} to rotation.")
        self._override_disabled.discard(node.name)
        self.update_bridge()

    # ------------------------------------------------------------------
    # Status query
    # ------------------------------------------------------------------

    def get_active_routes(self) -> list["Node"]:
        """Return the list of exit nodes currently in the bridge rotation."""
        all_exits = self.nm.exit_nodes()
        return [n for n in all_exits if n.name not in self._override_disabled]

    def get_disabled_nodes(self) -> list[str]:
        """Return node names currently removed from bridge routing."""
        return list(self._override_disabled)

    def force_enable(self, node_name: str) -> None:
        """Manually re-enable a node that was disabled by failover (without full failback)."""
        self._override_disabled.discard(node_name)

    def force_disable(self, node_name: str) -> None:
        """Manually remove a node from bridge routing without triggering failover logic."""
        self._override_disabled.add(node_name)

    # ------------------------------------------------------------------
    # AWG relay firewall
    # ------------------------------------------------------------------

    def _ensure_awg_relay_firewall(
        self, conn: "SSHConnection", bridge: "Node", awg_count: int
    ) -> None:
        """Open UDP ports for AWG relay dokodemo-door on a bridge node."""
        _global = self.cg._load_global()
        _bridge_cfg = _global.get("bridge", {})

        if bridge.single_inbound_port:
            port_start = int(_bridge_cfg.get("awg_relay_port_start_single", 51821))
        else:
            port_start = int(_bridge_cfg.get("awg_relay_port_start", 24441))

        port_end = port_start + awg_count - 1

        # Check if ufw is available
        _, _, rc = conn.exec("which ufw")
        if rc != 0:
            return  # no ufw — skip firewall management

        # Open port range
        rule = f"{port_start}:{port_end}/udp" if awg_count > 1 else f"{port_start}/udp"
        conn.exec(
            f"ufw allow {rule} comment 'DF AWG relay' 2>/dev/null || true"
        )
        print(f"[bridge] {bridge.name}: opened UDP {rule} for AWG relay")
