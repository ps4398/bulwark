"""
Config generator — renders Jinja2 templates for xray, Hysteria2 and AmneziaWG.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from core.node_manager import Node


# Region → third octet mapping for WireGuard subnets
REGION_OCTET: dict[str, int] = {
    "ru": 10,
    "fi": 20,
    "at": 30,
    "de": 40,
    "nl": 50,
    "us": 60,
}

BRIDGE_SHORT: dict[str, str] = {
    # Populated dynamically from node display_name at runtime.
    # Override by adding entries here: "my_bridge": "ShortLabel"
}

_REGION_FLAGS: dict[str, str] = {
    "fi": "\U0001f1eb\U0001f1ee", "at": "\U0001f1e6\U0001f1f9",
    "ru": "\U0001f1f7\U0001f1fa", "de": "\U0001f1e9\U0001f1ea",
    "nl": "\U0001f1f3\U0001f1f1", "us": "\U0001f1fa\U0001f1f8",
    "gb": "\U0001f1ec\U0001f1e7", "fr": "\U0001f1eb\U0001f1f7",
    "ch": "\U0001f1e8\U0001f1ed", "se": "\U0001f1f8\U0001f1ea",
    "no": "\U0001f1f3\U0001f1f4", "pl": "\U0001f1f5\U0001f1f1",
}


class ConfigGenerator:
    """Renders Jinja2 templates into deployable config files."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        self.config_dir = Path(config_dir)
        self.templates_dir = self.config_dir / "templates"
        self.secrets_dir = self.config_dir / "secrets"

        self._jinja = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    # ------------------------------------------------------------------
    # Secret loading
    # ------------------------------------------------------------------

    def load_secrets(self, node_name: str) -> dict:
        """Load the secrets YAML for a node; returns empty dict if absent."""
        path = self.secrets_dir / f"{node_name}.yaml"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    # ------------------------------------------------------------------
    # Per-protocol renderers
    # ------------------------------------------------------------------

    def generate_xray_exit(self, node: "Node", secrets: dict) -> str:
        """
        Render xray_exit.json.j2 for an exit node.
        *secrets* must contain: reality_private_key, clients list [{uuid, name}].
        """
        global_cfg = self._load_global()
        ports = global_cfg.get("ports", {})

        # Build client list — fall back to a single client from secrets
        clients = secrets.get("clients", [])
        if not clients:
            uuid = secrets.get("xray_uuid", "")
            if uuid:
                clients = [{"uuid": uuid, "name": "default"}]

        context: dict[str, Any] = {
            "node_name": node.name,
            "vless_reality_port": int(ports.get("vless_reality", 443)),
            "clients": clients,
            "reality_private_key": secrets.get("reality_private_key", ""),
            "reality_short_id": secrets.get("reality_short_id", ""),
            "reality_dest": secrets.get("reality_dest", ""),
            "reality_server_name": secrets.get("reality_server_name", ""),
        }
        tmpl = self._jinja.get_template("xray_exit.json.j2")
        return tmpl.render(**context)

    def generate_hysteria2(self, node: "Node", secrets: dict) -> str:
        """Render hysteria2.yaml.j2 for an exit node."""
        global_cfg = self._load_global()
        ports = global_cfg.get("ports", {})
        hy2_cfg = global_cfg.get("hysteria2", {})

        # Multi-user: hy2_users dict {name: password}
        # Fallback: single hysteria2_password → {"default": password}
        hy2_users = secrets.get("hy2_users", {})
        if not hy2_users:
            single_pass = secrets.get("hysteria2_password", "")
            if single_pass:
                hy2_users = {"default": single_pass}

        context: dict[str, Any] = {
            "node_name": node.name,
            "hysteria2_port": int(ports.get("hysteria2", 8443)),
            "hysteria2_password": secrets.get("hysteria2_password", ""),
            "hy2_users": hy2_users,
            "bandwidth_up": hy2_cfg.get("bandwidth_up", "1 gbps"),
            "bandwidth_down": hy2_cfg.get("bandwidth_down", "1 gbps"),
        }
        tmpl = self._jinja.get_template("hysteria2.yaml.j2")
        return tmpl.render(**context)

    def generate_amneziawg(self, node: "Node", secrets: dict) -> str:
        """Render amneziawg.conf.j2 for an exit node (AWG 2.0 params)."""
        global_cfg = self._load_global()
        ports = global_cfg.get("ports", {})
        awg_cfg = global_cfg.get("amneziawg", {})

        region_octet = REGION_OCTET.get(node.region, 99)
        outbound_iface = secrets.get("outbound_iface", "eth0")
        if "outbound_iface" not in secrets:
            import logging
            logging.getLogger(__name__).warning(
                "%s: outbound_iface not set in secrets, defaulting to 'eth0'. "
                "Run deploy to auto-detect or set manually.",
                node.name,
            )

        # Ensure AWG 2.0 obfuscation params exist in secrets
        secrets = self._ensure_awg2_params(secrets, node.name)

        context: dict[str, Any] = {
            "node_name": node.name,
            "region": node.region,
            "region_octet": region_octet,
            "private_key": secrets.get("awg_private_key", ""),
            "listen_port": int(secrets.get("awg_listen_port", ports.get("amneziawg", 51820))),
            "jc": int(awg_cfg.get("jc", 4)),
            "jmin": int(awg_cfg.get("jmin", 40)),
            "jmax": int(awg_cfg.get("jmax", 70)),
            "s1": int(secrets.get("awg_s1", 0)),
            "s2": int(secrets.get("awg_s2", 0)),
            "s3": int(secrets.get("awg_s3", 0)),
            "s4": int(secrets.get("awg_s4", 0)),
            "h1": int(secrets.get("awg_h1", 1)),
            "h2": int(secrets.get("awg_h2", 2)),
            "h3": int(secrets.get("awg_h3", 3)),
            "h4": int(secrets.get("awg_h4", 4)),
            # AWG 2.0: range-based H
            "h1_range": int(secrets.get("awg_h1_range", 0)),
            "h2_range": int(secrets.get("awg_h2_range", 0)),
            "h3_range": int(secrets.get("awg_h3_range", 0)),
            "h4_range": int(secrets.get("awg_h4_range", 0)),
            # AWG 2.0: Signature Packets
            "i1": secrets.get("awg_i1", ""),
            "i2": secrets.get("awg_i2", ""),
            "i3": secrets.get("awg_i3", ""),
            "outbound_iface": outbound_iface,
            "peers": secrets.get("awg_peers", []),
        }
        tmpl = self._jinja.get_template("amneziawg.conf.j2")
        return tmpl.render(**context)

    def generate_amneziawg_vpn_link(
        self,
        node: "Node",
        secrets: dict,
        peer: dict,
        relay_host: Optional[str] = None,
        relay_port: Optional[int] = None,
        relay_label: Optional[str] = None,
    ) -> str:
        """
        Generate a vpn:// deep link for AmneziaVPN client import.

        Encoding: JSON → zlib → 4-byte big-endian length header → base64url (no padding).

        If relay_host/relay_port are given, the link routes through a bridge node.
        Keys and AWG params always come from the exit node (e2e encryption).
        """
        import base64
        import json as _json
        import zlib

        global_cfg = self._load_global()
        awg_cfg = global_cfg.get("amneziawg", {})
        ports = global_cfg.get("ports", {})

        region_octet = REGION_OCTET.get(node.region, 99)
        jc   = int(awg_cfg.get("jc",   4))
        jmin = int(awg_cfg.get("jmin", 40))
        jmax = int(awg_cfg.get("jmax", 70))
        s1   = int(secrets.get("awg_s1", 0))
        s2   = int(secrets.get("awg_s2", 0))
        s3   = int(secrets.get("awg_s3", 0))
        s4   = int(secrets.get("awg_s4", 0))
        h1   = int(secrets.get("awg_h1", 1))
        h2   = int(secrets.get("awg_h2", 2))
        h3   = int(secrets.get("awg_h3", 3))
        h4   = int(secrets.get("awg_h4", 4))
        # AWG 2.0: range-based H
        h1_range = int(secrets.get("awg_h1_range", 0))
        h2_range = int(secrets.get("awg_h2_range", 0))
        h3_range = int(secrets.get("awg_h3_range", 0))
        h4_range = int(secrets.get("awg_h4_range", 0))
        # AWG 2.0: Signature Packets
        i1 = secrets.get("awg_i1", "")
        i2 = secrets.get("awg_i2", "")

        # Endpoint: bridge relay or direct
        host = relay_host or node.ip
        port = relay_port or int(secrets.get("awg_listen_port", ports.get("amneziawg", 51820)))

        client_addr    = peer.get("address", f"10.{region_octet}.0.2")
        client_privkey = peer.get("private_key", "")
        server_pubkey  = secrets.get("awg_public_key", "")

        # H values for config — range or fixed
        def _h_str(base: int, rng: int) -> str:
            return f"{base}-{base + rng}" if rng > 0 else str(base)

        h1_s = _h_str(h1, h1_range)
        h2_s = _h_str(h2, h2_range)
        h3_s = _h_str(h3, h3_range)
        h4_s = _h_str(h4, h4_range)

        # WireGuard INI config (goes inside last_config as the "config" field)
        wg_conf = (
            f"[Interface]\n"
            f"PrivateKey = {client_privkey}\n"
            f"Address = {client_addr}/32\n"
            f"DNS = 1.1.1.1, 1.0.0.1\n"
            f"Jc = {jc}\n"
            f"Jmin = {jmin}\n"
            f"Jmax = {jmax}\n"
            f"S1 = {s1}\n"
            f"S2 = {s2}\n"
            f"S3 = {s3}\n"
            f"S4 = {s4}\n"
            f"H1 = {h1_s}\n"
            f"H2 = {h2_s}\n"
            f"H3 = {h3_s}\n"
            f"H4 = {h4_s}\n"
        )
        if i1:
            wg_conf += f"i1 = {i1}\n"
        if i2:
            wg_conf += f"i2 = {i2}\n"
        wg_conf += (
            f"\n"
            f"[Peer]\n"
            f"PublicKey = {server_pubkey}\n"
            f"Endpoint = {host}:{port}\n"
            f"AllowedIPs = 0.0.0.0/0\n"
            f"PersistentKeepalive = 25\n"
        )

        # last_config is a JSON-stringified object — parsed by client via QJsonDocument::fromJson()
        last_config_obj: dict[str, Any] = {
            "Jc": jc,
            "Jmin": jmin,
            "Jmax": jmax,
            "S1": s1,
            "S2": s2,
            "S3": s3,
            "S4": s4,
            "H1": h1_s,
            "H2": h2_s,
            "H3": h3_s,
            "H4": h4_s,
            "allowed_ips": ["0.0.0.0/0", "::/0"],
            "client_ip": f"{client_addr}/32",
            "client_priv_key": client_privkey,
            "config": wg_conf,
            "hostName": host,
            "mtu": "1420",
            "persistent_keep_alive": "25",
            "port": port,
            "psk_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "server_pub_key": server_pubkey,
        }
        if i1:
            last_config_obj["i1"] = i1
        if i2:
            last_config_obj["i2"] = i2

        cfg = {
            "hostName": host,
            # No outer "port" — it would be interpreted as SSH port
            "description": self._awg_link_description(node, relay_label),
            "defaultContainer": "amnezia-awg2",
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "containers": [
                {
                    "container": "amnezia-awg2",
                    # Protocol key is always "awg" regardless of version (containerTypeToProtocolString)
                    "awg": {
                        "isThirdPartyConfig": True,
                        "protocolVersion": "2",
                        "last_config": _json.dumps(last_config_obj, ensure_ascii=False),
                        "port": str(port),
                        "transport_proto": "udp",
                    },
                }
            ],
        }

        json_bytes = _json.dumps(cfg, ensure_ascii=False).encode("utf-8")
        compressed = zlib.compress(json_bytes)
        header     = len(json_bytes).to_bytes(4, byteorder="big")
        encoded    = base64.urlsafe_b64encode(header + compressed).decode().rstrip("=")
        return f"vpn://{encoded}"

    @staticmethod
    def generate_awg_subscription_link(
        api_key: str,
        node_name: str = "",
        base_url: str = "",
        name: str = "Bulwark AWG",
        awg_prefix: str = "/awg-api",
    ) -> str:
        """Generate a Type 2 vpn:// link (API subscription pointer).

        Client imports this link once. On import, AmneziaVPN POSTs to
        api_endpoint with client's pubkey → server returns full config.
        To update config later, user re-imports the same link.

        If node_name is given, the link provisions on that specific node.
        awg_prefix is the randomised API path (from PORTAL_AWG_PREFIX env var).
        """
        import base64
        import json as _json
        import zlib

        if node_name:
            endpoint = f"{base_url}{awg_prefix}/{node_name}/"
        else:
            endpoint = f"{base_url}{awg_prefix}/"

        payload = {
            "config_version": 1.0,
            "api_endpoint": endpoint,
            "protocol": "awg",
            "name": name,
            "description": "AWG auto-config",
            "api_key": api_key,
        }

        json_bytes = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        compressed = zlib.compress(json_bytes)
        header = len(json_bytes).to_bytes(4, byteorder="big")
        encoded = base64.urlsafe_b64encode(header + compressed).decode().rstrip("=")
        return f"vpn://{encoded}"

    @staticmethod
    def _awg_link_description(node: "Node", relay_label: Optional[str] = None) -> str:
        """Build human-readable description for AWG vpn:// link.

        Format: "🇫🇮 Primary" or "🇫🇮 Fallback | via Bridge1"
        """
        flag = _REGION_FLAGS.get(node.region.lower(), node.region.upper())
        # Extract role: "Primary" / "Fallback" from display_name
        parts = node.display_name.split()
        role = parts[-1] if len(parts) > 1 else node.display_name
        desc = f"{flag} {role}"
        if relay_label:
            desc += f" | via {relay_label}"
        return desc

    def _ensure_awg2_params(self, secrets: dict, node_name: str) -> dict:
        """Generate AWG 2.0 obfuscation parameters if absent, persist to secrets."""
        awg_cfg = self._load_global().get("amneziawg", {})

        changed = False

        # S1/S2 — basic junk
        if not secrets.get("awg_s1"):
            secrets["awg_s1"] = random.randint(40, 100)
            changed = True
        if not secrets.get("awg_s2"):
            secrets["awg_s2"] = random.randint(40, 100)
            changed = True

        # S3/S4 — AWG 2.0 padding
        if "awg_s3" not in secrets:
            secrets["awg_s3"] = int(awg_cfg.get("s3_default", 32))
            changed = True
        if "awg_s4" not in secrets:
            secrets["awg_s4"] = int(awg_cfg.get("s4_default", 16))
            changed = True

        # H1-H4 base values + ranges — must not overlap
        h_range = int(awg_cfg.get("h_range_default", 100000))
        h4_range = int(awg_cfg.get("h4_range_default", 100000000))
        need_h_regen = not secrets.get("awg_h1")

        if "awg_h1_range" not in secrets:
            secrets["awg_h1_range"] = h_range
            secrets["awg_h2_range"] = h_range
            secrets["awg_h3_range"] = h_range
            secrets["awg_h4_range"] = h4_range
            # Existing H values may overlap with new ranges — check
            if not need_h_regen and secrets.get("awg_h1"):
                try:
                    self._validate_h_ranges(secrets)
                except ValueError:
                    need_h_regen = True
            changed = True

        if need_h_regen:
            # Sequential ranges with random gaps to avoid overlap
            gap = random.randint(100, 5000)
            base = random.randint(5, 50000)
            r = int(secrets.get("awg_h1_range", h_range))
            secrets["awg_h1"] = base
            secrets["awg_h2"] = base + r + gap
            secrets["awg_h3"] = base + 2 * (r + gap)
            secrets["awg_h4"] = base + 3 * (r + gap)
            changed = True

        # Signature Packets — from global config
        if "awg_i1" not in secrets:
            secrets["awg_i1"] = awg_cfg.get("i1", "")
            secrets["awg_i2"] = awg_cfg.get("i2", "")
            changed = True

        if changed:
            self._validate_awg2_params(secrets)
            path = self.secrets_dir / f"{node_name}.yaml"
            with open(path, "w", encoding="utf-8") as fh:
                yaml.dump(dict(sorted(secrets.items())), fh,
                          allow_unicode=True, default_flow_style=False)
        return secrets

    @staticmethod
    def _validate_h_ranges(secrets: dict) -> None:
        """Validate H1-H4 ranges don't overlap. Raises ValueError on overlap."""
        ranges = []
        for key in ["h1", "h2", "h3", "h4"]:
            base = int(secrets.get(f"awg_{key}", 0))
            r = int(secrets.get(f"awg_{key}_range", 0))
            ranges.append((base, base + r, key.upper()))

        for i, (a_min, a_max, a_name) in enumerate(ranges):
            for j, (b_min, b_max, b_name) in enumerate(ranges):
                if i >= j:
                    continue
                if a_min <= b_max and b_min <= a_max:
                    raise ValueError(
                        f"Диапазоны {a_name}=[{a_min}-{a_max}] и "
                        f"{b_name}=[{b_min}-{b_max}] пересекаются"
                    )

    def _validate_awg2_params(self, secrets: dict) -> None:
        """Validate AWG 2.0 parameters: S1/S2 collision, H ranges overlap."""
        s1 = int(secrets.get("awg_s1", 0))
        s2 = int(secrets.get("awg_s2", 0))
        if s1 + 56 == s2 or s2 + 56 == s1:
            raise ValueError(
                f"S1={s1} и S2={s2} конфликтуют: "
                f"S1+56==S2 или S2+56==S1 вызовет коллизию заголовков"
            )
        self._validate_h_ranges(secrets)

    def generate_xray_bridge(self, exit_nodes: list["Node"], bridge_node: Optional["Node"] = None) -> str:
        """
        Render xray_bridge.json.j2 for a bridge node.

        *bridge_node* identifies which bridge we're generating for (used to load
        per-bridge Reality keys and inbound port start).  When None the first
        entry in global.yaml bridge config is used as fallback.
        """
        global_cfg = self._load_global()
        bridge_cfg = global_cfg.get("bridge", {})
        ports = global_cfg.get("ports", {})

        # Per-bridge port start (from Node object if available, else global.yaml)
        if bridge_node is not None:
            port_start = bridge_node.inbound_port_start
        else:
            port_start = int(bridge_cfg.get("inbound_port_start", 24431))

        # Load bridge's own Reality secrets
        bridge_name = bridge_node.name if bridge_node else "management"
        bridge_secrets = self.load_secrets(bridge_name)

        nodes_ctx = []
        awg_nodes_ctx = []
        for node in exit_nodes:
            if not node.enabled or not node.is_exit:
                continue
            secrets = self.load_secrets(node.name)
            clients = secrets.get("clients", [])
            if not clients:
                uuid = secrets.get("xray_uuid", "")
                clients = [{"uuid": uuid, "name": "default"}] if uuid else []

            node_info = {
                "name": node.name,
                "ip": node.ip,
                "enabled": node.enabled,
                "bridge_port_offset": node.bridge_port_offset,
                "vless_port": int(ports.get("vless_reality", 443)),
                "clients": clients,
                "bridge_uuid": secrets.get("bridge_uuid", secrets.get("xray_uuid", "")),
                "reality_public_key": secrets.get("reality_public_key", ""),
                "reality_short_id": secrets.get("reality_short_id", ""),
                "reality_server_name": secrets.get("reality_server_name", ""),
            }
            nodes_ctx.append(node_info)
            if "amneziawg" in node.protocols:
                node_awg_port = int(secrets.get("awg_listen_port", ports.get("amneziawg", 51820)))
                awg_nodes_ctx.append({
                    "name": node.name,
                    "ip": node.ip,
                    "port_offset": node.bridge_port_offset,
                    "awg_port": node_awg_port,
                })

        # AWG relay port config
        awg_port = int(ports.get("amneziawg", 51820))
        if bridge_node and bridge_node.single_inbound_port:
            awg_relay_start = int(bridge_cfg.get("awg_relay_port_start_single", 51821))
        else:
            awg_relay_start = int(bridge_cfg.get("awg_relay_port_start", 24441))

        # Single-port mode: one inbound on port 443, UUID-based routing
        if bridge_node and bridge_node.single_inbound_port:
            return self._generate_xray_bridge_single_port(
                nodes_ctx, bridge_node, bridge_secrets,
                awg_nodes=awg_nodes_ctx, awg_relay_port_start=awg_relay_start,
                awg_port=awg_port,
            )

        context: dict[str, Any] = {
            "exit_nodes": nodes_ctx,
            "bridge_port_start": port_start,
            # Bridge inbound Reality settings (same for all inbounds on this bridge)
            "bridge_reality_private_key": bridge_secrets.get("reality_private_key", ""),
            "bridge_reality_public_key": bridge_secrets.get("reality_public_key", ""),
            "bridge_reality_short_id": bridge_secrets.get("reality_short_id", ""),
            "bridge_reality_dest": bridge_secrets.get("reality_dest", "www."),
            "bridge_reality_server_name": bridge_secrets.get("reality_server_name", "www."),
            # AWG relay via dokodemo-door
            "awg_nodes": awg_nodes_ctx,
            "awg_relay_port_start": awg_relay_start,
            "awg_port": awg_port,
        }
        tmpl = self._jinja.get_template("xray_bridge.json.j2")
        return tmpl.render(**context)

    def _generate_xray_bridge_single_port(
        self,
        nodes_ctx: list[dict],
        bridge_node: "Node",
        bridge_secrets: dict,
        *,
        awg_nodes: Optional[list[dict]] = None,
        awg_relay_port_start: int = 51821,
        awg_port: int = 51820,
    ) -> str:
        """
        Generate xray bridge config with a single inbound port, routing by UUID.
        Used for bridges where only port 443 is accessible (e.g. whitelist networks).
        Also includes dokodemo-door UDP inbounds for AWG relay.
        """
        import json as _json

        port = bridge_node.single_inbound_port
        node_clients = bridge_secrets.get("node_clients", {})
        dest = bridge_secrets.get("reality_dest", "")
        sni = bridge_secrets.get("reality_server_name", "")
        priv_key = bridge_secrets.get("reality_private_key", "")
        short_id = bridge_secrets.get("reality_short_id", "")

        # Build clients list: one UUID per exit node
        clients = []
        for node in nodes_ctx:
            nc = node_clients.get(node["name"], {})
            client_uuid = nc.get("uuid", "")
            if client_uuid:
                clients.append({
                    "id": client_uuid,
                    "flow": "xtls-rprx-vision",
                    "email": node["name"],
                })

        # Build outbounds
        outbounds = []
        for node in nodes_ctx:
            outbounds.append({
                "tag": f"out-{node['name']}",
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": node["ip"],
                        "port": node["vless_port"],
                        "users": [{
                            "id": node["bridge_uuid"],
                            "flow": "xtls-rprx-vision",
                            "encryption": "none",
                        }],
                    }],
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": node["reality_server_name"],
                        "fingerprint": "chrome",
                        "show": False,
                        "publicKey": node["reality_public_key"],
                        "shortId": node["reality_short_id"],
                        "spiderX": "",
                    },
                },
            })
        outbounds.append({"tag": "blocked", "protocol": "blackhole", "settings": {}})

        # Build routing rules: by user email
        rules: list[dict] = [
            {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
        ]
        for node in nodes_ctx:
            rules.append({
                "type": "field",
                "user": [node["name"]],
                "outboundTag": f"out-{node['name']}",
            })

        # AWG relay inbounds (dokodemo-door UDP)
        awg_inbounds: list[dict] = []
        for i, awg_node in enumerate(awg_nodes or []):
            awg_inbounds.append({
                "tag": f"awg-{awg_node['name']}",
                "listen": "0.0.0.0",
                "port": awg_relay_port_start + awg_node.get("port_offset", i),
                "protocol": "dokodemo-door",
                "settings": {
                    "address": awg_node["ip"],
                    "port": awg_node.get("awg_port", awg_port),
                    "network": "udp",
                },
            })

        cfg = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "tag": "in-443",
                "listen": "0.0.0.0",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "clients": clients,
                    "decryption": "none",
                    "fallbacks": [{"dest": 8080, "xver": 0}],
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": dest,
                        "xver": 0,
                        "serverNames": [sni],
                        "privateKey": priv_key,
                        "shortIds": [short_id],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            }] + awg_inbounds,
            "outbounds": outbounds,
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": rules,
            },
            "policy": {
                "levels": {
                    "0": {
                        "handshakeTimeout": 4,
                        "connIdle": 300,
                        "uplinkOnly": 1,
                        "downlinkOnly": 1,
                    }
                }
            },
        }
        return _json.dumps(cfg, indent=2, ensure_ascii=False)

    def _build_subscription_uris(
        self,
        exit_nodes: list["Node"],
        bridge_nodes: Optional[list["Node"]] = None,
    ) -> list[str]:
        """
        Build the full list of proxy URIs for the subscription.

        For each enabled exit node:
          - VLESS+Reality direct
          - Hysteria2 direct
          - For each enabled bridge node: VLESS via bridge (Reality chain)

        AmneziaWG is a layer-3 tunnel and cannot be represented in a V2Ray
        subscription; distribute AWG configs via `bulwark secrets show <node>`.
        """
        import urllib.parse

        global_cfg = self._load_global()
        ports = global_cfg.get("ports", {})
        vless_port = int(ports.get("vless_reality", 443))
        hy2_port = int(ports.get("hysteria2", 8443))

        # Pre-load bridge secrets for chain URIs
        active_bridges: list[dict] = []
        for bridge in (bridge_nodes or []):
            if not bridge.enabled or not bridge.is_bridge:
                continue
            bs = self.load_secrets(bridge.name)
            if not bs.get("reality_public_key"):
                continue
            active_bridges.append({
                "node": bridge,
                "access_uuid": bs.get("bridge_access_uuid", ""),
                "pub_key": bs.get("reality_public_key", ""),
                "short_id": bs.get("reality_short_id", ""),
                "sni": bs.get("reality_server_name", "www."),
                # single_inbound_port bridges use per-node UUIDs
                "node_clients": bs.get("node_clients", {}),
            })

        # Index of each exit node in the ordered list (determines bridge port)
        enabled_exits = [n for n in exit_nodes if n.enabled and n.is_exit]

        lines: list[str] = []

        for idx, node in enumerate(enabled_exits):
            secrets = self.load_secrets(node.name)
            if not secrets:
                continue

            uuid = secrets.get("xray_uuid", "")
            pub_key = secrets.get("reality_public_key", "")
            short_id = secrets.get("reality_short_id", "")
            sni = secrets.get("reality_server_name", "")
            hy2_pass = secrets.get("hysteria2_password", "")
            _REGION_FLAGS = {
                "fi": "🇫🇮", "at": "🇦🇹", "ru": "🇷🇺", "de": "🇩🇪",
                "nl": "🇳🇱", "us": "🇺🇸", "gb": "🇬🇧", "fr": "🇫🇷",
                "ch": "🇨🇭", "se": "🇸🇪", "no": "🇳🇴", "pl": "🇵🇱",
            }
            flag = _REGION_FLAGS.get(node.region.lower(), node.region.upper())
            suffix = f" {node.priority}" if node.priority > 1 else ""

            # --- Direct VLESS+Reality ---
            if "vless_reality" in node.protocols and uuid and pub_key:
                params = urllib.parse.urlencode({
                    "encryption": "none",
                    "flow": "xtls-rprx-vision",
                    "security": "reality",
                    "sni": sni,
                    "fp": "chrome",
                    "pbk": pub_key,
                    "sid": short_id,
                    "type": "tcp",
                    "headerType": "none",
                })
                tag = urllib.parse.quote(f"{flag} VLESS{suffix}")
                lines.append(f"vless://{uuid}@{node.ip}:{vless_port}?{params}#{tag}")

            # --- Direct Hysteria2 ---
            if "hysteria2" in node.protocols and hy2_pass:
                hy2_params = urllib.parse.urlencode({"insecure": "1"})
                tag = urllib.parse.quote(f"{flag} HY2{suffix}")
                lines.append(
                    f"hysteria2://{urllib.parse.quote(hy2_pass)}@{node.ip}:{hy2_port}"
                    f"?{hy2_params}#{tag}"
                )

            # --- Via each bridge (VLESS chain) ---
            for bridge_info in active_bridges:
                bridge = bridge_info["node"]
                b_pub = bridge_info["pub_key"]
                b_sid = bridge_info["short_id"]
                b_sni = bridge_info["sni"]

                if not b_pub:
                    continue

                # Single-port bridges use per-node UUID + fixed port 443
                if bridge.single_inbound_port:
                    node_clients = bridge_info.get("node_clients", {})
                    nc = node_clients.get(node.name, {})
                    b_uuid = nc.get("uuid", "")
                    b_port = bridge.single_inbound_port
                else:
                    b_uuid = bridge_info["access_uuid"]
                    b_port = bridge.inbound_port_start + node.bridge_port_offset

                if not b_uuid:
                    continue

                b_params = urllib.parse.urlencode({
                    "encryption": "none",
                    "flow": "xtls-rprx-vision",
                    "security": "reality",
                    "sni": b_sni,
                    "fp": "chrome",
                    "pbk": b_pub,
                    "sid": b_sid,
                    "type": "tcp",
                    "headerType": "none",
                })
                bridge_short = BRIDGE_SHORT.get(
                    bridge.name,
                    bridge.display_name.replace(" Bridge", "").replace(" Cloud", ""),
                )
                b_tag = urllib.parse.quote(f"{flag} {bridge_short}{suffix}")
                lines.append(
                    f"vless://{b_uuid}@{bridge.ip}:{b_port}?{b_params}#{b_tag}"
                )

        return lines

    def generate_subscription(
        self,
        exit_nodes: list["Node"],
        bridge_nodes: Optional[list["Node"]] = None,
    ) -> str:
        """
        Generate a V2Ray-compatible base64 subscription string.

        Includes for each exit node: VLESS direct, HY2 direct,
        and VLESS-via-bridge for every enabled bridge node.
        """
        import base64

        lines = self._build_subscription_uris(exit_nodes, bridge_nodes)
        raw = "\n".join(lines)
        return base64.b64encode(raw.encode("utf-8")).decode("utf-8")

    def generate_subscription_plain(
        self,
        exit_nodes: list["Node"],
        bridge_nodes: Optional[list["Node"]] = None,
    ) -> list[str]:
        """Return raw (non-encoded) proxy URIs for human inspection."""
        return self._build_subscription_uris(exit_nodes, bridge_nodes)

    def _all_exit_nodes_cached(self) -> list["Node"]:
        """Helper to get exit nodes for index calculation."""
        from core.node_manager import NodeManager
        nm = NodeManager(self.config_dir)
        return nm.exit_nodes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_global(self) -> dict:
        path = self.config_dir / "global.yaml"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
