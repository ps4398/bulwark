"""
Node deployer — installs and configures the full protocol stack on remote nodes.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from core.config_gen import ConfigGenerator, REGION_OCTET
from core.node_manager import NodeManager

if TYPE_CHECKING:
    from core.node_manager import Node


SCRIPTS_DIR = Path(__file__).parent.parent / "deploy" / "scripts"
SECRETS_DIR = Path(__file__).parent.parent / "config" / "secrets"


class NodeDeployer:
    """Handles full-stack deployment to remote nodes."""

    def __init__(
        self,
        node_manager: NodeManager,
        config_gen: Optional[ConfigGenerator] = None,
    ) -> None:
        self.nm = node_manager
        self.cg = config_gen or ConfigGenerator()

    # ------------------------------------------------------------------
    # Secret generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_uuid() -> str:
        """Generate a random UUID4 string."""
        return str(uuid.uuid4())

    @staticmethod
    def generate_hysteria2_password() -> str:
        """Generate a cryptographically random 32-char password."""
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(secrets.choice(alphabet) for _ in range(32))

    @staticmethod
    def generate_reality_keys() -> tuple[str, str]:
        """
        Generate an X25519 key-pair for XTLS-Reality.

        Tries to call `xray x25519` locally first; falls back to the
        cryptography library if xray is not installed on the management host.
        Returns (private_key_b64, public_key_b64).
        """
        try:
            result = subprocess.run(
                ["xray", "x25519"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                # Expected output:
                #   Private key: <base64>
                #   Public key:  <base64>
                private_key = ""
                public_key = ""
                for line in lines:
                    if line.lower().startswith("private key"):
                        private_key = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("public key"):
                        public_key = line.split(":", 1)[1].strip()
                if private_key and public_key:
                    return private_key, public_key
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: use the cryptography library
        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
            import base64

            private_key_obj = X25519PrivateKey.generate()
            private_bytes = private_key_obj.private_bytes_raw()
            public_bytes = private_key_obj.public_key().public_bytes_raw()
            private_b64 = base64.urlsafe_b64encode(private_bytes).decode().rstrip("=")
            public_b64 = base64.urlsafe_b64encode(public_bytes).decode().rstrip("=")
            return private_b64, public_b64
        except ImportError:
            pass

        # Last resort: return placeholders that the user must fill in
        placeholder = "<REPLACE_WITH_XRAY_X25519_KEY>"
        return placeholder, placeholder

    @staticmethod
    def generate_reality_short_id() -> str:
        """Generate an 8-byte hex short ID for Reality."""
        return secrets.token_hex(8)

    # ------------------------------------------------------------------
    # Secrets persistence
    # ------------------------------------------------------------------

    def save_node_secrets(self, node_name: str, secrets_dict: dict) -> None:
        """Save generated secrets to config/secrets/<node_name>.yaml."""
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        path = SECRETS_DIR / f"{node_name}.yaml"

        # Merge with existing secrets (don't overwrite unrelated keys)
        existing: dict = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                existing = yaml.safe_load(fh) or {}
        existing.update(secrets_dict)

        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(existing, fh, default_flow_style=False, allow_unicode=True)

    def load_node_secrets(self, node_name: str) -> dict:
        """Load secrets for a node; return empty dict if not found."""
        path = SECRETS_DIR / f"{node_name}.yaml"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def ensure_node_secrets(self, node: "Node") -> dict:
        """
        Load existing secrets or generate fresh ones.
        All generated secrets are persisted immediately.
        """
        existing = self.load_node_secrets(node.name)

        changed = False

        if "xray_uuid" not in existing:
            existing["xray_uuid"] = self.generate_uuid()
            changed = True

        if "bridge_uuid" not in existing:
            existing["bridge_uuid"] = self.generate_uuid()
            changed = True

        if "reality_private_key" not in existing or "reality_public_key" not in existing:
            priv, pub = self.generate_reality_keys()
            existing["reality_private_key"] = priv
            existing["reality_public_key"] = pub
            changed = True

        if "reality_short_id" not in existing:
            existing["reality_short_id"] = self.generate_reality_short_id()
            changed = True

        if "reality_dest" not in existing:
            # SNI domain must be reachable from the node's IP.
            # Default is empty — set a proper domain in config/secrets/<node>.yaml
            existing["reality_dest"] = ""
            existing["reality_server_name"] = ""
            changed = True

        if "hysteria2_password" not in existing:
            existing["hysteria2_password"] = self.generate_hysteria2_password()
            changed = True

        if "clients" not in existing:
            existing["clients"] = [
                {"uuid": existing["xray_uuid"], "name": "default"},
                {"uuid": existing["bridge_uuid"], "name": "bridge"},
            ]
            changed = True

        if changed:
            self.save_node_secrets(node.name, existing)

        return existing

    # ------------------------------------------------------------------
    # Script upload helper
    # ------------------------------------------------------------------

    def _upload_and_run_script(
        self,
        node: "Node",
        script_name: str,
        env_vars: Optional[dict] = None,
        timeout: int = 300,
    ) -> tuple[str, str, int]:
        """Upload a shell script and execute it on the remote node."""
        script_path = SCRIPTS_DIR / script_name
        if not script_path.exists():
            raise FileNotFoundError(f"Deploy script not found: {script_path}")

        remote_path = f"/tmp/bw_{script_name}"

        with open(script_path, "r", encoding="utf-8") as fh:
            content = fh.read()

        with self.nm.ssh(node) as conn:
            conn.upload_content(content, remote_path)
            # Make executable
            conn.exec(f"chmod +x {remote_path}")

            # Prepend env vars if any
            env_prefix = ""
            if env_vars:
                env_prefix = " ".join(
                    f'{k}="{v}"' for k, v in env_vars.items()
                ) + " "

            stdout, stderr, code = conn.exec(
                f"{env_prefix}bash {remote_path}",
                timeout=timeout,
            )
            # Clean up
            conn.exec(f"rm -f {remote_path}")
            return stdout, stderr, code

    # ------------------------------------------------------------------
    # Individual protocol deployers
    # ------------------------------------------------------------------

    def deploy_base(self, node: "Node") -> None:
        """Run install_base.sh on the node (system preparation)."""
        print(f"[deploy] {node.name}: running base installation...")
        ports = self.cg._load_global().get("ports", {})
        bridge_cfg = self.cg._load_global().get("bridge", {})
        env = {
            "VLESS_PORT": str(ports.get("vless_reality", 443)),
            "HY2_PORT": str(ports.get("hysteria2", 8443)),
            "AWG_PORT": str(ports.get("amneziawg", 51820)),
            "BRIDGE_PORT_START": str(bridge_cfg.get("inbound_port_start", 24431)),
            "BRIDGE_PORT_END": str(bridge_cfg.get("inbound_port_start", 24431) + 10),
        }
        stdout, stderr, code = self._upload_and_run_script(node, "install_base.sh", env_vars=env)
        if code != 0:
            raise RuntimeError(
                f"Base installation failed on {node.name} (exit {code}):\n{stderr}"
            )
        print(f"[deploy] {node.name}: base installation complete.")

    def deploy_xray(self, node: "Node") -> None:
        """Install xray-core, upload config, enable systemd service."""
        print(f"[deploy] {node.name}: deploying xray...")
        secrets = self.ensure_node_secrets(node)

        global_cfg = self.cg._load_global()
        xray_version = global_cfg.get("software", {}).get("xray_version", "26.2.6")

        # 1. Install binary
        stdout, stderr, code = self._upload_and_run_script(
            node, "install_xray.sh", env_vars={"XRAY_VERSION": xray_version}
        )
        if code != 0:
            raise RuntimeError(
                f"xray installation failed on {node.name} (exit {code}):\n{stderr}"
            )

        # 2. Generate and upload config
        config_content = self.cg.generate_xray_exit(node, secrets)
        xray_cfg_path = self.cg._load_global().get("xray", {}).get(
            "config_path", "/usr/local/etc/xray/config.json"
        )
        with self.nm.ssh(node) as conn:
            conn.upload_content(config_content, xray_cfg_path)
            conn.exec("systemctl daemon-reload")
            conn.exec("systemctl enable xray")
            out, err, rc = conn.exec("systemctl restart xray")
            if rc != 0:
                raise RuntimeError(
                    f"Failed to start xray on {node.name}:\n{err}"
                )

        print(f"[deploy] {node.name}: xray deployed and started.")

    def deploy_hysteria2(self, node: "Node") -> None:
        """Download hysteria2, generate TLS cert, upload config, enable service."""
        print(f"[deploy] {node.name}: deploying Hysteria2...")
        secrets = self.ensure_node_secrets(node)

        # 1. Install binary + generate cert
        stdout, stderr, code = self._upload_and_run_script(node, "install_hysteria2.sh")
        if code != 0:
            raise RuntimeError(
                f"Hysteria2 installation failed on {node.name} (exit {code}):\n{stderr}"
            )

        # 2. Generate and upload config
        config_content = self.cg.generate_hysteria2(node, secrets)
        with self.nm.ssh(node) as conn:
            conn.upload_content(config_content, "/etc/hysteria/config.yaml")
            conn.exec("systemctl daemon-reload")
            conn.exec("systemctl enable hysteria2")
            out, err, rc = conn.exec("systemctl restart hysteria2")
            if rc != 0:
                raise RuntimeError(
                    f"Failed to start hysteria2 on {node.name}:\n{err}"
                )

        print(f"[deploy] {node.name}: Hysteria2 deployed and started.")

    def deploy_amneziawg(self, node: "Node") -> None:
        """Install AmneziaWG, upload config, enable service."""
        print(f"[deploy] {node.name}: deploying AmneziaWG...")
        secrets = self.ensure_node_secrets(node)

        # Ensure AWG private key exists
        if "awg_private_key" not in secrets:
            # Generate on remote after install
            awg_private_key = None
        else:
            awg_private_key = secrets["awg_private_key"]

        # 1. Install
        stdout, stderr, code = self._upload_and_run_script(
            node, "install_amneziawg.sh"
        )
        if code != 0:
            raise RuntimeError(
                f"AmneziaWG installation failed on {node.name} (exit {code}):\n{stderr}"
            )

        # 2. Generate WG key on remote if not already present
        with self.nm.ssh(node) as conn:
            if not awg_private_key:
                out, err, rc = conn.exec("awg genkey")
                if rc != 0:
                    # Try wg genkey as fallback
                    out, err, rc = conn.exec("wg genkey")
                if rc != 0:
                    raise RuntimeError(
                        f"Failed to generate AWG private key on {node.name}:\n{err}"
                    )
                awg_private_key = out.strip()
                # Get public key — write to tempfile to avoid shell injection
                conn.exec(f"printf '%s' {awg_private_key!r} > /tmp/df_awg_priv.tmp")
                pub_out, pub_err, pub_rc = conn.exec(
                    "awg pubkey < /tmp/df_awg_priv.tmp || wg pubkey < /tmp/df_awg_priv.tmp"
                )
                conn.exec("rm -f /tmp/df_awg_priv.tmp")
                if pub_rc != 0:
                    raise RuntimeError(f"Failed to derive AWG public key on {node.name}: {pub_err}")
                awg_public_key = pub_out.strip()
                secrets["awg_private_key"] = awg_private_key
                secrets["awg_public_key"] = awg_public_key
                self.save_node_secrets(node.name, secrets)

            # 3. Auto-generate default client peer if none configured
            if not secrets.get("awg_peers"):
                print(f"[deploy] {node.name}: generating default AWG client peer...")
                out, err, rc = conn.exec("awg genkey")
                if rc != 0:
                    out, err, rc = conn.exec("wg genkey")
                if rc == 0:
                    client_privkey = out.strip()
                    conn.exec(f"printf '%s' {client_privkey!r} > /tmp/df_awg_client.tmp")
                    pub_out, _, pub_rc = conn.exec(
                        "awg pubkey < /tmp/df_awg_client.tmp"
                        " || wg pubkey < /tmp/df_awg_client.tmp"
                    )
                    conn.exec("rm -f /tmp/df_awg_client.tmp")
                    if pub_rc == 0:
                        region_octet = REGION_OCTET.get(node.region, 99)
                        secrets["awg_peers"] = [{
                            "name": "default",
                            "private_key": client_privkey,
                            "public_key": pub_out.strip(),
                            "address": f"10.{region_octet}.0.2",
                        }]
                        self.save_node_secrets(node.name, secrets)

            # 4. Auto-detect outbound interface for NAT
            if "outbound_iface" not in secrets:
                iface_out, _, _ = conn.exec(
                    "ip route | awk '/default/{print $5; exit}'"
                )
                iface = iface_out.strip() or "eth0"
                if iface != "eth0":
                    secrets["outbound_iface"] = iface
                    self.save_node_secrets(node.name, secrets)
                    print(f"[deploy] {node.name}: detected outbound interface: {iface}")

            # 5. Upload config (with peers)
            config_content = self.cg.generate_amneziawg(node, secrets)
            conn.upload_content(config_content, "/etc/amnezia/amneziawg/awg0.conf")
            conn.exec("chmod 600 /etc/amnezia/amneziawg/awg0.conf")
            conn.exec("systemctl daemon-reload")
            conn.exec("systemctl enable wg-quick@awg0")
            out, err, rc = conn.exec("systemctl restart wg-quick@awg0")
            if rc != 0:
                raise RuntimeError(
                    f"Failed to start AmneziaWG on {node.name}:\n{err}"
                )

        print(f"[deploy] {node.name}: AmneziaWG deployed and started.")

    # ------------------------------------------------------------------
    # Full stack
    # ------------------------------------------------------------------

    def install_xray_binary(self, node: "Node") -> None:
        """
        Install xray binary and enable systemd unit on a bridge node.
        Does NOT upload a config — bridge config is pushed by BridgeManager.
        """
        global_cfg = self.cg._load_global()
        xray_version = global_cfg.get("software", {}).get("xray_version", "26.2.6")
        stdout, stderr, code = self._upload_and_run_script(
            node, "install_xray.sh", env_vars={"XRAY_VERSION": xray_version}
        )
        if code != 0:
            raise RuntimeError(
                f"xray installation failed on {node.name} (exit {code}):\n{stderr}"
            )
        with self.nm.ssh(node) as conn:
            conn.exec("systemctl daemon-reload && systemctl enable xray")
        print(f"[deploy] {node.name}: xray binary installed and service enabled.")

    def deploy_all(self, node: "Node") -> None:
        """Run the full deployment sequence for a node."""
        if node.is_bridge:
            raise ValueError(
                f"Node '{node.name}' is a bridge — use deploy_bridge() instead."
            )

        print(f"[deploy] {node.name}: starting full stack deployment...")
        self.deploy_base(node)
        self.deploy_xray(node)
        self.deploy_hysteria2(node)
        self.deploy_amneziawg(node)
        print(f"[deploy] {node.name}: full deployment complete.")

    def redeploy(self, node: "Node") -> None:
        """
        Re-upload configs and restart services without re-installing binaries.
        Useful after config changes.
        """
        print(f"[deploy] {node.name}: redeploying configs...")
        secrets = self.ensure_node_secrets(node)

        global_cfg = self.cg._load_global()
        xray_cfg_path = global_cfg.get("xray", {}).get(
            "config_path", "/usr/local/etc/xray/config.json"
        )

        with self.nm.ssh(node) as conn:
            if node.is_exit:
                if "vless_reality" in node.protocols:
                    config_content = self.cg.generate_xray_exit(node, secrets)
                    conn.upload_content(config_content, xray_cfg_path)
                    out, err, rc = conn.exec("systemctl restart xray")
                    if rc != 0:
                        raise RuntimeError(
                            f"Failed to restart xray on {node.name}:\n{err}"
                        )

                if "hysteria2" in node.protocols:
                    config_content = self.cg.generate_hysteria2(node, secrets)
                    conn.upload_content(config_content, "/etc/hysteria/config.yaml")
                    out, err, rc = conn.exec("systemctl restart hysteria2")
                    if rc != 0:
                        raise RuntimeError(
                            f"Failed to restart hysteria2 on {node.name}:\n{err}"
                        )

                if "amneziawg" in node.protocols:
                    config_content = self.cg.generate_amneziawg(node, secrets)
                    conn.upload_content(config_content, "/etc/amnezia/amneziawg/awg0.conf")
                    conn.exec("chmod 600 /etc/amnezia/amneziawg/awg0.conf")
                    out, err, rc = conn.exec("systemctl restart wg-quick@awg0")
                    if rc != 0:
                        raise RuntimeError(
                            f"Failed to restart wg-quick@awg0 on {node.name}:\n{err}"
                        )

        print(f"[deploy] {node.name}: redeploy complete.")

    def sync_config(self, node: "Node") -> None:
        """
        Upload updated configs WITHOUT restarting services.
        Use for config preview / staged rollouts.
        """
        print(f"[deploy] {node.name}: syncing configs (no restart)...")
        secrets = self.ensure_node_secrets(node)

        global_cfg = self.cg._load_global()
        xray_cfg_path = global_cfg.get("xray", {}).get(
            "config_path", "/usr/local/etc/xray/config.json"
        )

        with self.nm.ssh(node) as conn:
            if node.is_exit:
                if "vless_reality" in node.protocols:
                    config_content = self.cg.generate_xray_exit(node, secrets)
                    conn.upload_content(config_content, xray_cfg_path + ".pending")

                if "hysteria2" in node.protocols:
                    config_content = self.cg.generate_hysteria2(node, secrets)
                    conn.upload_content(config_content, "/etc/hysteria/config.yaml.pending")

                if "amneziawg" in node.protocols:
                    config_content = self.cg.generate_amneziawg(node, secrets)
                    conn.upload_content(config_content, "/etc/amnezia/amneziawg/awg0.conf.pending")

        print(f"[deploy] {node.name}: config sync complete (files written as *.pending).")

    def show_remote_config(self, node: "Node", protocol: str) -> str:
        """Read a remote config file and return its contents."""
        global_cfg = self.cg._load_global()
        xray_cfg_path = global_cfg.get("xray", {}).get(
            "config_path", "/usr/local/etc/xray/config.json"
        )
        config_paths = {
            "vless_reality": xray_cfg_path,
            "xray": xray_cfg_path,
            "hysteria2": "/etc/hysteria/config.yaml",
            "amneziawg": "/etc/amnezia/amneziawg/awg0.conf",
        }
        remote_path = config_paths.get(protocol)
        if not remote_path:
            raise ValueError(
                f"Unknown protocol '{protocol}'. "
                f"Valid options: {', '.join(config_paths.keys())}"
            )
        with self.nm.ssh(node) as conn:
            out, err, rc = conn.exec(f"cat {remote_path}")
            if rc != 0:
                raise RuntimeError(
                    f"Could not read {remote_path} on {node.name}: {err}"
                )
            return out
