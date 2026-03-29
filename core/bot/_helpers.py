"""Shared utilities, FSM states and auth middleware for the bot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

# Project root (for config/, .env, deploy/ etc.)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

from core.config_gen import REGION_FLAGS as _FLAG_MAP


def _flag(region: str) -> str:
    return _FLAG_MAP.get(region.lower(), "🌐")


def _fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} PB"


def _parse_gb(size_str: str) -> float:
    """'1.2 GB' → 1.2, '800.0 MB' → 0.78"""
    try:
        val, unit = size_str.strip().split()
        val = float(val)
        unit = unit.upper()
        if unit == "TB":
            return val * 1024
        if unit == "GB":
            return val
        if unit == "MB":
            return val / 1024
        if unit == "KB":
            return val / (1024 * 1024)
        return val / (1024 ** 3)
    except Exception:
        return 0.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build inline keyboard from rows of (text, callback_data) tuples."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])


# ---------------------------------------------------------------------------
# FSM States for node-add wizard
# ---------------------------------------------------------------------------


class NodeAddStates(StatesGroup):
    waiting_name = State()
    waiting_ip = State()
    waiting_region = State()
    waiting_ssh_password = State()
    waiting_ssh_key_path = State()
    waiting_sni = State()


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseMiddleware):
    """Block messages and callbacks from non-allowed users."""

    def __init__(self, allowed_ids: set[int]):
        self.allowed_ids = allowed_ids

    async def __call__(
        self, handler, event: TelegramObject, data: dict[str, Any],
    ):
        user = getattr(event, "from_user", None)
        if user is None:
            return
        if self.allowed_ids and user.id not in self.allowed_ids:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещён")
            elif isinstance(event, Message):
                await event.answer("⛔ Нет доступа.")
            return
        return await handler(event, data)
