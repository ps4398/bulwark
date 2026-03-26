"""
AWG API user management — stores api_key → user mapping for subscription links.

Storage: config/awg_users.json (git-tracked, no secrets — api_keys are revocable tokens).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional


class AWGUserManager:
    """Manages AWG API subscription users and their api_keys."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        self._path = Path(config_dir) / "awg_users.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            return {"users": {}}
        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "users" not in data:
            data["users"] = {}
        return data

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(self._path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        Path(tmp).replace(self._path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_user(
        self,
        name: str,
        assigned_nodes: Optional[list[str]] = None,
        max_peers: int = 5,
    ) -> dict:
        """Create a new AWG API user with a fresh api_key."""
        users = self._data["users"]
        if name in users:
            raise ValueError(f"User '{name}' already exists")

        api_key = uuid.uuid4().hex
        user = {
            "name": name,
            "api_key": api_key,
            "assigned_nodes": assigned_nodes or [],
            "max_peers": max_peers,
            "peers": [],  # [{pubkey, device_uuid, node, address, created_at}]
            "active": True,
        }
        users[name] = user
        self._save()
        return user

    def get_user(self, name: str) -> Optional[dict]:
        return self._data["users"].get(name)

    def get_user_by_api_key(self, api_key: str) -> Optional[dict]:
        for user in self._data["users"].values():
            if user.get("api_key") == api_key and user.get("active", True):
                return user
        return None

    def list_users(self) -> list[dict]:
        return list(self._data["users"].values())

    def revoke_user(self, name: str) -> bool:
        user = self._data["users"].get(name)
        if not user:
            return False
        user["active"] = False
        user["api_key"] = ""
        self._save()
        return True

    def add_peer_record(
        self,
        user_name: str,
        pubkey: str,
        node_name: str,
        address: str,
        device_uuid: str = "",
    ) -> None:
        """Record a provisioned peer for a user (tracking only)."""
        user = self._data["users"].get(user_name)
        if not user:
            raise ValueError(f"User '{user_name}' not found")
        user["peers"].append({
            "pubkey": pubkey,
            "node": node_name,
            "address": address,
            "device_uuid": device_uuid,
        })
        self._save()

    def find_peer_by_pubkey(self, user_name: str, pubkey: str) -> Optional[dict]:
        """Find a previously provisioned peer by pubkey for a user."""
        user = self._data["users"].get(user_name)
        if not user:
            return None
        for peer in user.get("peers", []):
            if peer["pubkey"] == pubkey:
                return peer
        return None

    def reload(self) -> None:
        self._data = self._load()
