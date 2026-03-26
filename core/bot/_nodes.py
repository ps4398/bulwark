"""Node management: enable/disable, wizard (FSM), remove."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.bot._helpers import NodeAddStates, _PROJECT_ROOT, _flag, _kb


class NodesMixin:
    """Node enable/disable, add wizard, remove."""

    _VALID_NODE_NAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
    _VALID_IPV4 = re.compile(
        r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
    )

    # ------------------------------------------------------------------
    # Node list / toggle
    # ------------------------------------------------------------------

    async def _node_mgmt(
        self, chat_id: int | str, msg_id: Optional[int] = None,
    ) -> None:
        all_nodes = self.nm.all_nodes()
        lines = ["🖥️ <b>Управление нодами</b>\n"]
        btns: list[tuple[str, str]] = []
        for n in all_nodes:
            disabled = self._is_node_disabled(n.name)
            state_icon = "🔴" if disabled else "🟢"
            role_icon = "🌉" if n.is_bridge else "🚀"
            lines.append(f"{state_icon}{role_icon} {n.name}")
            label = f"{'🔴' if disabled else '🟢'} {n.name}"
            btns.append((label, f"node_toggle:{n.name}"))

        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("➕ Добавить", "node_add_start"), ("➖ Удалить", "node_rm_start")])
        rows.append([("← Управление", "mgmt")])
        await self._show(chat_id, msg_id, "\n".join(lines), _kb(rows))

    async def _node_toggle_confirm(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        disabled = self._is_node_disabled(node_name)
        action_word = "включить" if disabled else "отключить"
        effect = (
            "Нода будет добавлена в мониторинг и подписку."
            if disabled else
            "Нода будет исключена из мониторинга, подписки и bridge-роутинга."
        )
        text = (
            f"⚠️ <b>Подтвердить: {action_word} {node.name}?</b>\n\n"
            f"{effect}"
        )
        markup = _kb([[
            ("✅ Подтвердить", f"node_toggle_ok:{node_name}"),
            ("❌ Отмена", "node_mgmt"),
        ]])
        await self._edit(chat_id, msg_id, text, markup)

    async def _node_toggle_exec(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        was_disabled = self._is_node_disabled(node_name)

        if was_disabled:
            self._disabled_nodes.discard(node_name)
            action_done = "включена"
            if node.is_exit and self.bm:
                try:
                    await self._run(self.bm.force_enable, node_name)
                except Exception as e:
                    await self._edit(chat_id, msg_id, f"❌ Ошибка bridge force_enable: {e}")
                    return
        else:
            self._disabled_nodes.add(node_name)
            action_done = "отключена"
            if node.is_exit and self.bm:
                try:
                    await self._run(self.bm.force_disable, node_name)
                except Exception as e:
                    await self._edit(chat_id, msg_id, f"❌ Ошибка bridge force_disable: {e}")
                    return

        self._save_node_overrides()
        icon = "🟢" if was_disabled else "🔴"
        result = f"{icon} <b>{node.name}</b> {action_done}"
        markup = _kb([[("← Ноды", "node_mgmt"), ("🏠 Меню", "menu")]])
        await self._edit(chat_id, msg_id, result, markup)

    # ------------------------------------------------------------------
    # Add wizard — callback steps
    # ------------------------------------------------------------------

    async def _node_add_start(
        self, chat_id: int | str, msg_id: int,
    ) -> None:
        text = "➕ <b>Добавить ноду</b>\n\nВыбери тип:"
        markup = _kb([
            [("🌉 Bridge", "node_add_type:bridge"),
             ("🚀 Exit", "node_add_type:exit")],
            [("❌ Отмена", "node_mgmt")],
        ])
        await self._edit(chat_id, msg_id, text, markup)

    async def _node_add_type(
        self, chat_id: int | str, msg_id: int, role: str, state: FSMContext,
    ) -> None:
        if role not in ("bridge", "exit"):
            return
        await state.update_data(role=role, msg_id=msg_id)
        await state.set_state(NodeAddStates.waiting_name)
        await self._edit(
            chat_id, msg_id,
            f"➕ <b>Новая нода ({role})</b>\n\n"
            "Введи имя (snake_case, напр. <code>amsterdam_exit</code>):\n\n"
            "<i>/cancel для отмены</i>",
        )

    async def _node_add_priority(
        self, chat_id: int | str, msg_id: int, value: str, state: FSMContext,
    ) -> None:
        data = await state.get_data()
        await state.update_data(priority=int(value))
        await self._edit(
            chat_id, msg_id,
            f"➕ <b>{data['name']}</b>\n\nSSH авторизация:",
            _kb([
                [("🔑 Пароль", "node_add_ssh:password"),
                 ("🔐 Ключ", "node_add_ssh:key")],
                [("❌ Отмена", "node_add_cancel")],
            ]),
        )

    async def _node_add_ssh_method(
        self, chat_id: int | str, msg_id: int, method: str, state: FSMContext,
    ) -> None:
        data = await state.get_data()
        if method == "password":
            await state.set_state(NodeAddStates.waiting_ssh_password)
            await self._edit(
                chat_id, msg_id,
                f"➕ <b>{data['name']}</b>\n\nВведи SSH-пароль (сообщение будет удалено):",
            )
        elif method == "key":
            await state.set_state(NodeAddStates.waiting_ssh_key_path)
            await self._edit(
                chat_id, msg_id,
                f"➕ <b>{data['name']}</b>\n\nВведи путь к SSH-ключу (напр. <code>config/secrets/df_key</code>):",
            )

    async def _node_add_bridge_mode(
        self, chat_id: int | str, msg_id: int, mode: str, state: FSMContext,
    ) -> None:
        if mode == "single":
            await state.update_data(single_inbound_port=443)
        await self._node_add_summary(chat_id, msg_id, state)

    async def _node_add_protocols_render(
        self, chat_id: int | str, msg_id: int, state: FSMContext,
    ) -> None:
        data = await state.get_data()
        protos = data.get("protocols", [])
        labels = {
            "vless_reality": "VLESS+Reality",
            "hysteria2": "Hysteria2",
            "amneziawg": "AmneziaWG",
        }
        btns = []
        for key, label in labels.items():
            on = key in protos
            icon = "✅" if on else "⬜"
            btns.append((f"{icon} {label}", f"node_add_ptoggle:{key}"))
        rows = [btns]
        rows.append([("Далее →", "node_add_pdone")])
        rows.append([("❌ Отмена", "node_add_cancel")])
        await self._edit(
            chat_id, msg_id,
            f"➕ <b>{data['name']}</b>\n\nПротоколы для деплоя:",
            _kb(rows),
        )

    async def _node_add_toggle_protocol(
        self, chat_id: int | str, msg_id: int, proto: str, state: FSMContext,
    ) -> None:
        data = await state.get_data()
        protos = list(data.get("protocols", []))
        if proto in protos:
            protos.remove(proto)
        else:
            protos.append(proto)
        await state.update_data(protocols=protos)
        await self._node_add_protocols_render(chat_id, msg_id, state)

    async def _node_add_summary(
        self, chat_id: int | str, msg_id: int, state: FSMContext,
    ) -> None:
        d = await state.get_data()
        role = d["role"]
        role_ru = "Bridge" if role == "bridge" else "Exit"
        flag = _flag(d.get("region", ""))

        lines = [
            f"➕ <b>Подтверди добавление ноды</b>\n",
            f"Имя: <code>{d['name']}</code>",
            f"IP: <code>{d['ip']}</code>",
            f"Регион: {flag} {d.get('region', '').upper()}",
            f"Тип: {role_ru}",
        ]
        if role == "exit":
            priority = d.get("priority", 1)
            lines.append(f"Приоритет: {priority} ({'primary' if priority == 1 else 'fallback'})")
            protos = d.get("protocols", [])
            lines.append(f"Протоколы: {', '.join(protos) or 'нет'}")
        else:
            sp = d.get("single_inbound_port")
            lines.append(f"Порт: {'single-port 443' if sp else 'multi-port'}")

        ssh_type = "ключ" if d.get("ssh_key_path") else "пароль"
        lines.append(f"SSH: {ssh_type}")

        markup = _kb([
            [("✅ Подтвердить и деплоить", "node_add_confirm")],
            [("❌ Отмена", "node_add_cancel")],
        ])
        await self._edit(chat_id, msg_id, "\n".join(lines), markup)

    async def _node_add_confirm(
        self, chat_id: int | str, msg_id: int, state: FSMContext,
    ) -> None:
        d = await state.get_data()
        name = d["name"]
        role = d["role"]
        await state.clear()

        if not self.deployer:
            await self._edit(chat_id, msg_id, "❌ Deployer не инициализирован.")
            return

        # 1. Build node_data for nodes.yaml
        node_data: dict = {
            "name": name,
            "display_name": name.replace("_", " ").title(),
            "ip": d["ip"],
            "ssh_port": 22,
            "ssh_user": "root",
            "role": role,
            "region": d.get("region", ""),
            "priority": d.get("priority", 1),
            "enabled": True,
            "description": f"Added via bot ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})",
        }

        # SSH auth
        if d.get("ssh_key_path"):
            node_data["ssh_key_path"] = d["ssh_key_path"]
            node_data["ssh_password_env"] = ""
        else:
            env_var = f"{name.upper()}_SSH_PASSWORD"
            node_data["ssh_key_path"] = ""
            node_data["ssh_password_env"] = env_var
            os.environ[env_var] = d.get("ssh_password", "")
            env_path = _PROJECT_ROOT / ".env"
            try:
                with open(env_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{env_var}={d['ssh_password']}\n")
            except Exception as e:
                print(f"[bot] .env write error: {e}")

        if role == "bridge":
            node_data["protocols"] = []
            node_data["inbound_port_start"] = int(
                self.cfg.get("bridge", {}).get("inbound_port_start", 20000)
            )
            if d.get("single_inbound_port"):
                node_data["single_inbound_port"] = d["single_inbound_port"]
        else:
            node_data["protocols"] = d.get("protocols", [])
            max_offset = max(
                (n.bridge_port_offset for n in self.nm.all_nodes() if n.is_exit),
                default=-1,
            )
            node_data["bridge_port_offset"] = max_offset + 1

        # 2. Add to inventory
        await self._edit(chat_id, msg_id, f"⏳ Добавление <b>{name}</b> в inventory...")
        try:
            self.nm.add_node(name, node_data)
            node = self.nm.get_node(name)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка добавления: {e}")
            return

        # 3. Secrets + Reality SNI
        def _ensure_secrets_with_sni():
            self.deployer.ensure_node_secrets(node)
            sni = d.get("reality_sni", "")
            if sni:
                import yaml
                secrets_path = _PROJECT_ROOT / "config" / "secrets" / f"{name}.yaml"
                if secrets_path.exists():
                    with open(secrets_path, "r", encoding="utf-8") as f:
                        sec = yaml.safe_load(f) or {}
                    sec["reality_dest"] = f"{sni}:443"
                    sec["reality_server_name"] = sni
                    with open(secrets_path, "w", encoding="utf-8") as f:
                        yaml.dump(sec, f, default_flow_style=False, allow_unicode=True)

        # 4. Deploy steps
        if role == "bridge":
            steps = [
                ("Base install", lambda: self.deployer.deploy_base(node)),
                ("Xray binary", lambda: self.deployer.install_xray_binary(node)),
                ("Секреты", _ensure_secrets_with_sni),
            ]
            if self.bm:
                steps.append(("Bridge config", lambda: self.bm.update_bridge()))
        else:
            steps = [
                ("Base install", lambda: self.deployer.deploy_base(node)),
                ("Секреты + SNI", _ensure_secrets_with_sni),
            ]
            protos = d.get("protocols", [])
            if "vless_reality" in protos:
                steps.append(("VLESS+Reality", lambda: self.deployer.deploy_xray(node)))
            if "hysteria2" in protos:
                steps.append(("Hysteria2", lambda: self.deployer.deploy_hysteria2(node)))
            if "amneziawg" in protos:
                steps.append(("AmneziaWG", lambda: self.deployer.deploy_amneziawg(node)))
            if self.bm:
                steps.append(("Bridge routing", lambda: self.bm.update_bridge()))

        done: list[str] = []
        failed = False
        for i, (label, fn) in enumerate(steps):
            progress_lines = []
            for j, (sl, _) in enumerate(steps):
                if j < i:
                    progress_lines.append(f"✅ {sl}")
                elif j == i:
                    progress_lines.append(f"⏳ {sl}...")
                else:
                    progress_lines.append(f"◻ {sl}")
            await self._edit(
                chat_id, msg_id,
                f"🚀 <b>Деплой {name}</b>\n\n" + "\n".join(progress_lines),
            )
            try:
                await self._run(fn)
                done.append(label)
            except Exception as e:
                progress_lines[i] = f"❌ {label}: {e}"
                for j in range(i + 1, len(steps)):
                    progress_lines[j] = f"⏭ {steps[j][0]} (пропущен)"
                await self._edit(
                    chat_id, msg_id,
                    f"⚠️ <b>Деплой {name} — ошибка</b>\n\n" + "\n".join(progress_lines),
                    _kb([[("🖥 Ноды", "node_mgmt"), ("🏠 Меню", "menu")]]),
                )
                failed = True
                break

        if not failed:
            result_lines = [f"✅ {s}" for s in done]
            await self._edit(
                chat_id, msg_id,
                f"✅ <b>{name} успешно добавлена!</b>\n\n" + "\n".join(result_lines),
                _kb([[("🖥 Ноды", "node_mgmt"), ("🏠 Меню", "menu")]]),
            )
            self._audit(f"node_add: {name} ({role})")

    # ------------------------------------------------------------------
    # Add wizard — FSM text steps
    # ------------------------------------------------------------------

    async def _fsm_node_name(self, message: Message, state: FSMContext) -> None:
        data, msg_id, chat_id = await self._fsm_ctx(message, state)
        name = (message.text or "").strip().lower()
        if not self._VALID_NODE_NAME.match(name):
            await self._edit(
                chat_id, msg_id,
                "❌ Имя должно быть snake_case (a-z, 0-9, _), 3-31 символ.\nПопробуй ещё раз:",
            )
            return
        existing = [n.name for n in self.nm.all_nodes()]
        if name in existing:
            await self._edit(chat_id, msg_id, f"❌ Нода <code>{name}</code> уже существует.\nВведи другое имя:")
            return
        await state.update_data(name=name)
        await state.set_state(NodeAddStates.waiting_ip)
        await self._edit(chat_id, msg_id, f"➕ <b>{name}</b>\n\nВведи IP-адрес сервера:")

    async def _fsm_node_ip(self, message: Message, state: FSMContext) -> None:
        data, msg_id, chat_id = await self._fsm_ctx(message, state)
        ip = (message.text or "").strip()
        if not self._VALID_IPV4.match(ip):
            await self._edit(chat_id, msg_id, "❌ Неверный IPv4 формат. Попробуй ещё раз:")
            return
        await state.update_data(ip=ip)
        await state.set_state(NodeAddStates.waiting_region)
        await self._edit(
            chat_id, msg_id,
            f"➕ <b>{data['name']}</b> ({ip})\n\nВведи код региона (ru/fi/at/de/nl/us/...):",
        )

    async def _fsm_node_region(self, message: Message, state: FSMContext) -> None:
        data, msg_id, chat_id = await self._fsm_ctx(message, state)
        region = (message.text or "").strip().lower()
        if len(region) < 2 or len(region) > 5:
            await self._edit(chat_id, msg_id, "❌ Код региона: 2-5 символов. Попробуй ещё раз:")
            return
        await state.update_data(region=region)
        await state.set_state(None)

        if data.get("role") == "exit":
            await self._edit(
                chat_id, msg_id,
                f"➕ <b>{data['name']}</b>\n\nПриоритет ноды:",
                _kb([
                    [("1 — Primary", "node_add_priority:1"),
                     ("2 — Fallback", "node_add_priority:2")],
                    [("❌ Отмена", "node_add_cancel")],
                ]),
            )
        else:
            await self._edit(
                chat_id, msg_id,
                f"➕ <b>{data['name']}</b>\n\nSSH авторизация:",
                _kb([
                    [("🔑 Пароль", "node_add_ssh:password"),
                     ("🔐 Ключ", "node_add_ssh:key")],
                    [("❌ Отмена", "node_add_cancel")],
                ]),
            )

    async def _fsm_node_ssh_password(self, message: Message, state: FSMContext) -> None:
        data, msg_id, chat_id = await self._fsm_ctx(message, state)
        await state.update_data(ssh_password=(message.text or "").strip())
        await state.set_state(NodeAddStates.waiting_sni)
        await self._edit(
            chat_id, msg_id,
            f"➕ <b>{data['name']}</b>\n\n"
            "Введи домен для Reality SNI (должен быть доступен с IP сервера, напр. <code>google.com</code>):",
        )

    async def _fsm_node_ssh_key(self, message: Message, state: FSMContext) -> None:
        data, msg_id, chat_id = await self._fsm_ctx(message, state)
        path = (message.text or "").strip()
        if not Path(path).exists():
            await self._edit(chat_id, msg_id, f"❌ Файл <code>{path}</code> не найден.\nВведи путь ещё раз:")
            return
        await state.update_data(ssh_key_path=path)
        await state.set_state(NodeAddStates.waiting_sni)
        await self._edit(
            chat_id, msg_id,
            f"➕ <b>{data['name']}</b>\n\n"
            "Введи домен для Reality SNI (должен быть доступен с IP сервера, напр. <code>google.com</code>):",
        )

    async def _fsm_node_sni(self, message: Message, state: FSMContext) -> None:
        data, msg_id, chat_id = await self._fsm_ctx(message, state)
        sni = (message.text or "").strip().lower()
        if not sni or " " in sni:
            await self._edit(chat_id, msg_id, "❌ Введи корректный домен (напр. <code>google.com</code>):")
            return
        await state.update_data(reality_sni=sni)
        await state.set_state(None)

        if data.get("role") == "bridge":
            await self._edit(
                chat_id, msg_id,
                f"➕ <b>{data['name']}</b>\n\nРежим bridge-порта:",
                _kb([
                    [("Multi-port", "node_add_bmode:multi"),
                     ("Single-port 443", "node_add_bmode:single")],
                    [("❌ Отмена", "node_add_cancel")],
                ]),
            )
        else:
            await state.update_data(
                protocols=["vless_reality", "hysteria2", "amneziawg"],
            )
            await self._node_add_protocols_render(chat_id, msg_id, state)

    # ------------------------------------------------------------------
    # Remove node
    # ------------------------------------------------------------------

    async def _node_rm_start(
        self, chat_id: int | str, msg_id: int,
    ) -> None:
        nodes = self.nm.all_nodes()
        btns = []
        for n in nodes:
            icon = "🌉" if n.is_bridge else "🚀"
            btns.append((f"{icon} {n.name}", f"node_rm_sel:{n.name}"))
        rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
        rows.append([("❌ Отмена", "node_mgmt")])
        await self._edit(
            chat_id, msg_id,
            "➖ <b>Удалить ноду</b>\n\nВыбери ноду для удаления:",
            _kb(rows),
        )

    async def _node_rm_select(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        role_ru = "Bridge" if node.is_bridge else "Exit"
        flag = _flag(node.region)
        if node.is_exit:
            warning = "Нода будет убрана из bridge-роутинга и подписок."
        else:
            warning = "Bridge будет убран, routing на остальных бриджах обновится."

        text = (
            f"⚠️ <b>Удалить {node.name}?</b>\n\n"
            f"IP: <code>{node.ip}</code>\n"
            f"Тип: {role_ru}\n"
            f"Регион: {flag} {node.region.upper()}\n\n"
            f"⚠️ {warning}\n"
            f"Сервисы на сервере <b>не будут</b> остановлены."
        )
        markup = _kb([
            [("🗑 Подтвердить удаление", f"node_rm_ok:{node_name}")],
            [("❌ Отмена", "node_mgmt")],
        ])
        await self._edit(chat_id, msg_id, text, markup)

    async def _node_rm_confirm(
        self, chat_id: int | str, msg_id: int, node_name: str,
    ) -> None:
        node = await self._resolve_node(chat_id, msg_id, node_name)
        if not node:
            return

        await self._edit(chat_id, msg_id, f"⏳ Удаление <b>{node_name}</b>...")

        try:
            self.nm.remove_node(node_name)
        except Exception as e:
            await self._edit(chat_id, msg_id, f"❌ Ошибка удаления: {e}")
            return

        self._disabled_nodes.discard(node_name)
        self._save_node_overrides()

        if self.bm:
            try:
                await self._run(self.bm.update_bridge)
            except Exception as e:
                await self._edit(
                    chat_id, msg_id,
                    f"⚠️ <b>{node_name}</b> удалена, но bridge update не удался: {e}",
                    _kb([[("🖥 Ноды", "node_mgmt"), ("🏠 Меню", "menu")]]),
                )
                return

        await self._edit(
            chat_id, msg_id,
            f"✅ <b>{node_name}</b> удалена из inventory.\n\n"
            f"Сервисы на сервере не затронуты — деком. вручную.",
            _kb([[("🖥 Ноды", "node_mgmt"), ("🏠 Меню", "menu")]]),
        )
        self._audit(f"node_remove: {node_name}")
