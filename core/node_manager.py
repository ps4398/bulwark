"""
Node Manager — loads node inventory from config/nodes.yaml and provides
SSH connectivity helpers using paramiko (key-based or password auth).
"""

from __future__ import annotations

import io
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional

import paramiko
import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Node:
    name: str
    display_name: str
    ip: str
    ssh_port: int
    ssh_user: str
    ssh_key_path: str          # path to private key, or "" if password auth
    ssh_password_env: str      # env var name holding the password (or "")
    role: str                  # "bridge" | "exit"
    region: str
    priority: int
    enabled: bool
    protocols: list[str]
    description: str = ""
    inbound_port_start: int = 0   # bridge only: set by setup.sh or nodes.yaml
    single_inbound_port: Optional[int] = None  # bridge only: if set, use one port with UUID routing
    bridge_port_offset: int = 0  # exit only: stable offset for bridge relay ports

    # Resolved once at runtime
    _key_path_expanded: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        if self.ssh_key_path:
            self._key_path_expanded = str(Path(self.ssh_key_path).expanduser())

    @property
    def is_exit(self) -> bool:
        return self.role == "exit"

    @property
    def is_bridge(self) -> bool:
        return self.role == "bridge"

    @property
    def ssh_password(self) -> Optional[str]:
        """Resolve SSH password from environment variable at runtime."""
        if not self.ssh_password_env:
            return None
        return os.environ.get(self.ssh_password_env)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Node":
        return cls(
            name=name,
            display_name=data.get("display_name", name),
            ip=data["ip"],
            ssh_port=int(data.get("ssh_port", 22)),
            ssh_user=data.get("ssh_user", "root"),
            ssh_key_path=data.get("ssh_key_path", ""),
            ssh_password_env=data.get("ssh_password_env", ""),
            role=data.get("role", "exit"),
            region=data.get("region", ""),
            priority=int(data.get("priority", 1)),
            enabled=bool(data.get("enabled", True)),
            protocols=list(data.get("protocols", [])),
            description=data.get("description", ""),
            inbound_port_start=int(data.get("inbound_port_start", 0)),
            single_inbound_port=int(data["single_inbound_port"]) if data.get("single_inbound_port") else None,
            bridge_port_offset=int(data.get("bridge_port_offset", 0)),
        )


# ---------------------------------------------------------------------------
# SSH connection context manager
# ---------------------------------------------------------------------------

class SSHConnection:
    """
    Wrapper around a paramiko SSHClient.
    Supports both key-based auth (preferred) and password auth.
    Auth priority: key → password.
    """

    def __init__(self, node: Node) -> None:
        self.node = node
        self.client: paramiko.SSHClient = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self) -> "SSHConnection":
        node = self.node
        key_path = node._key_path_expanded
        password = node.ssh_password

        use_key = bool(key_path and Path(key_path).exists())
        use_password = bool(password)

        if not use_key and not use_password:
            if key_path:
                raise FileNotFoundError(
                    f"SSH key not found for '{node.name}': {key_path}\n"
                    f"Set ssh_password_env in nodes.yaml as fallback, or fix the key path."
                )
            raise ValueError(
                f"No SSH auth method for '{node.name}': "
                f"neither ssh_key_path nor ssh_password_env is configured."
            )

        kwargs: dict = dict(
            hostname=node.ip,
            port=node.ssh_port,
            username=node.ssh_user,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        if use_key:
            kwargs["key_filename"] = key_path
        else:
            kwargs["password"] = password

        self.client.connect(**kwargs)
        return self

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    def __enter__(self) -> "SSHConnection":
        return self.connect()

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def exec(self, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        """Run a command; returns (stdout, stderr, exit_code)."""
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err, exit_code

    # ------------------------------------------------------------------
    # File transfer
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a local file to the remote path."""
        sftp = self.client.open_sftp()
        try:
            # Ensure remote directory exists
            remote_dir = str(Path(remote_path).parent).replace("\\", "/")
            self._sftp_mkdir_p(sftp, remote_dir)
            sftp.put(str(local_path), remote_path)
        finally:
            sftp.close()

    def upload_content(self, content: str, remote_path: str) -> None:
        """Write string content directly to a remote file (no temp file needed)."""
        sftp = self.client.open_sftp()
        try:
            remote_dir = str(Path(remote_path).parent).replace("\\", "/")
            self._sftp_mkdir_p(sftp, remote_dir)
            buf = io.BytesIO(content.encode("utf-8"))
            sftp.putfo(buf, remote_path)
        finally:
            sftp.close()

    @staticmethod
    def _sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
        """Recursively create remote directories (like mkdir -p)."""
        dirs = []
        current = remote_dir
        while True:
            try:
                sftp.stat(current)
                break
            except IOError:
                dirs.append(current)
                parent = current.rsplit("/", 1)[0]
                if not parent or parent == current:
                    break
                current = parent
        for d in reversed(dirs):
            try:
                sftp.mkdir(d)
            except IOError:
                pass  # already exists (race condition)


# ---------------------------------------------------------------------------
# NodeManager
# ---------------------------------------------------------------------------

class NodeManager:
    """Loads and provides access to the node inventory."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        self.config_dir = Path(config_dir)
        self._nodes: dict[str, Node] = {}
        self.load_nodes()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_nodes(self) -> None:
        nodes_path = self.config_dir / "nodes.yaml"
        if not nodes_path.exists():
            raise FileNotFoundError(f"Nodes config not found: {nodes_path}")
        with open(nodes_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data or "nodes" not in data:
            raise ValueError("nodes.yaml must contain a top-level 'nodes' key.")
        self._nodes = {
            name: Node.from_dict(name, node_data)
            for name, node_data in data["nodes"].items()
        }

    def reload(self) -> None:
        """Reload nodes from disk (useful in long-running processes)."""
        self.load_nodes()

    # ------------------------------------------------------------------
    # Persistence — add / remove
    # ------------------------------------------------------------------

    def add_node(self, name: str, node_data: dict) -> "Node":
        """Добавить ноду в nodes.yaml и перезагрузить inventory."""
        nodes_path = self.config_dir / "nodes.yaml"
        with open(nodes_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if name in data["nodes"]:
            raise ValueError(f"Нода '{name}' уже существует")
        data["nodes"][name] = node_data
        with open(nodes_path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        self.load_nodes()
        return self._nodes[name]

    def remove_node(self, name: str) -> None:
        """Удалить ноду из nodes.yaml, удалить её secrets-файл, перезагрузить."""
        nodes_path = self.config_dir / "nodes.yaml"
        with open(nodes_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if name not in data["nodes"]:
            raise KeyError(f"Нода '{name}' не найдена")
        del data["nodes"][name]
        with open(nodes_path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        secrets_path = self.config_dir / "secrets" / f"{name}.yaml"
        if secrets_path.exists():
            secrets_path.unlink()
        self.load_nodes()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_node(self, name: str) -> Node:
        if name not in self._nodes:
            available = ", ".join(self._nodes.keys())
            raise KeyError(
                f"Node '{name}' not found. Available nodes: {available}"
            )
        return self._nodes[name]

    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def exit_nodes(self) -> list[Node]:
        return [n for n in self._nodes.values() if n.is_exit and n.enabled]

    def bridge_nodes(self) -> list[Node]:
        return [n for n in self._nodes.values() if n.is_bridge and n.enabled]

    def enabled_nodes(self) -> list[Node]:
        return [n for n in self._nodes.values() if n.enabled]

    # ------------------------------------------------------------------
    # SSH helpers (stateless wrappers — open + close per call)
    # ------------------------------------------------------------------

    @contextmanager
    def ssh(self, node: Node) -> Generator[SSHConnection, None, None]:
        """Context manager: yields an open SSHConnection for *node*."""
        conn = SSHConnection(node)
        try:
            conn.connect()
            yield conn
        finally:
            conn.close()

    def exec_command(
        self, node: Node, cmd: str, timeout: int = 60
    ) -> tuple[str, str, int]:
        """Open SSH, run *cmd*, return (stdout, stderr, exit_code)."""
        with self.ssh(node) as conn:
            return conn.exec(cmd, timeout=timeout)

    def upload_file(
        self, node: Node, local_path: str, remote_path: str
    ) -> None:
        """Open SSH and upload a local file to the remote path."""
        with self.ssh(node) as conn:
            conn.upload_file(local_path, remote_path)

    def upload_content(
        self, node: Node, content: str, remote_path: str
    ) -> None:
        """Open SSH and write *content* string to *remote_path*."""
        with self.ssh(node) as conn:
            conn.upload_content(content, remote_path)

    # ------------------------------------------------------------------
    # Connectivity check
    # ------------------------------------------------------------------

    def check_ssh_connectivity(self, node: Node, timeout: int = 10) -> bool:
        """Return True if a TCP connection to node SSH port succeeds."""
        try:
            with socket.create_connection(
                (node.ip, node.ssh_port), timeout=timeout
            ):
                return True
        except (OSError, socket.timeout):
            return False
