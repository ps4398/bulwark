# Bulwark

> [!WARNING]
> Данный проект подготовлен в научно-технических целях. Bulwark является инструментом
> автоматизации развёртывания и мониторинга серверного ПО (xray-core, Hysteria2,
> AmneziaWG) на серверах, принадлежащих пользователю. Разработчик не несёт
> ответственности за иное использование утилиты. Перед применением убедитесь, что
> ваши действия соответствуют законодательству вашей страны.

Утилита для оркестрации распределённой сетевой инфраструктуры. Автоматизирует развёртывание, конфигурирование и мониторинг **VLESS+Reality** (xray-core), **Hysteria2** и **AmneziaWG** на удалённых серверах через SSH.

🇬🇧 [English version](README.en.md)

## Что делает

- **Развёртывание протоколов** — установка и конфигурирование xray-core, Hysteria2, AmneziaWG 2.0 на удалённых нодах по SSH
- **Bridge-relay маршрутизация** — настройка relay-бриджей (xray) для проксирования трафика к exit-нодам
- **Telegram-бот** — административная панель: статус нод, трафик, рестарт сервисов, логи, обновление бинарников, спидтесты
- **Wizard нод** — добавление и деплой новых серверов (bridge или exit) через пошаговый диалог в боте
- **Генерация конфигов** — Jinja2-шаблоны, автоматическая генерация и ротация секретов (UUID, X25519, WireGuard ключи)
- **Мониторинг и failover** — асинхронные ICMP + TCP проверки, автоматическое переключение при отказе ноды


## Архитектура

```
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Bridge 1 │ │ Bridge 2 │ │ Bridge 3 │   Relay-ноды
        │ mgmt+бот │ │ single   │ │ single   │   xray relay
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │             │             │
             └─────────────┼─────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  Exit 1  │ │  Exit 2  │ │  Exit 3  │   Exit-ноды
        │          │ │          │ │          │   VLESS + HY2 + AWG
        └──────────┘ └──────────┘ └──────────┘
```

**Режимы бриджей:**
- **Multi-port** — каждая exit-нода получает выделенный порт на бридже
- **Single-port (443)** — маршрутизация по UUID, весь трафик через один порт

## Быстрый старт

**Требования:** Ubuntu 20.04+ / Debian 11+ VPS с root-доступом, токен Telegram-бота от [@BotFather](https://t.me/BotFather).

```bash
# 1. Скачайте и распакуйте на VPS
wget -qO- https://github.com/ps4398/bulwark/archive/refs/heads/main.tar.gz | tar xz
cd bulwark-main

# 2. Запустите интерактивную настройку (конфигурирует management-бридж)
chmod +x setup.sh
./setup.sh

# 3. Запустите бот
systemctl start bulwark-bot

# 4. Откройте Telegram → /start → добавляйте exit-ноды через wizard
```

## Конфигурация

| Файл | Назначение |
|------|-----------|
| `.env` | Telegram-токен, SSH-пароли, credentials портала |
| `config/nodes.yaml` | Инвентарь нод (IP, роль, регион, SSH auth) |
| `config/global.yaml` | Мониторинг, порты, версии ПО, AWG-параметры |
| `config/secrets/<node>.yaml` | Автоматически сгенерированные секреты (UUID, ключи) |
| `config/templates/*.j2` | Jinja2-шаблоны конфигов протоколов |

Все конфиги генерируются `setup.sh` при первом запуске. Дополнительные ноды настраиваются через wizard в Telegram-боте.

## Команды CLI

```bash
python cli.py <команда>
```

| Команда | Описание |
|---------|----------|
| `status [node]` | Статус всех нод или детально по одной |
| `deploy <node> [--protocol xray\|hy2\|awg\|all]` | Деплой стека протоколов на ноду |
| `redeploy <node> <protocol>` | Передеплой конкретного протокола |
| `config sync <node>` | Синхронизация конфига на ноду |
| `config show <node> <protocol>` | Показ конфига протокола |
| `monitor` | Запуск непрерывного мониторинга |
| `bridge update` | Обновить routing на всех бриджах |
| `bridge deploy <node>` | Деплой новой bridge-ноды |
| `bridge routes` | Показать текущие маршруты |
| `node list` | Список нод |
| `node add` | Интерактивное добавление ноды |
| `sub show` | Содержимое подписки |
| `sub generate` | Генерация подписки |
| `sub push` | Push подписки на портал |
| `secrets show <node>` | Секреты ноды |
| `secrets regenerate <node>` | Перегенерация секретов |
| `awg add-peer <node>` | Добавить AWG-пира |
| `awg gen-link <node>` | Сгенерировать AWG 2.0 ссылку |
| `awg list-peers <node>` | Список AWG-пиров |
| `awg sub add\|list\|show\|revoke` | Управление AWG API подписками |
| `management install <node>` | Установить Bulwark на удалённую ноду |
| `management status <node>` | Статус management-сервиса |
| `bot start [--no-monitor]` | Запустить Telegram-бот |

## Telegram-бот (административная панель)

Бот доступен только администратору (ID задаётся при настройке). Управление через inline-клавиатуры:

| Категория | Возможности |
|-----------|-------------|
| **Мониторинг** | Статус нод (live ICMP+TCP), трафик по нодам, uptime-статистика |
| **Сервисы** | Рестарт xray / hysteria2 / awg, просмотр логов, очистка journalctl |
| **Обслуживание** | Обновление бинарников (GitHub API), спидтест, sysinfo, reboot |
| **Инфраструктура** | Wizard добавления нод, включение/отключение, failover |
| **Конфигурация** | Push bridge-конфигов, push подписки, reload портала |
| **AmneziaWG** | Управление пирами, генерация vpn:// ссылок |

## Портал подписок

Опциональный Flask-сервис. Запускается на management-бридже за reverse proxy.

**Эндпоинты** (пути генерируются случайно при setup):
- `GET /<prefix>/<token>` — V2Ray подписка (base64)
- `POST /<prefix>/` — AWG auto-provisioning

```bash
python cli.py management install <bridge_node>
# Слушает 127.0.0.1:8787, проксируется через nginx / Cloudflare Tunnel
```

## Структура проекта

```
bulwark/
├── cli.py                          # CLI точка входа (Click + Rich)
├── setup.sh                        # Интерактивная первоначальная настройка
├── requirements.txt                # Python-зависимости
├── .env.example                    # Шаблон переменных окружения
├── config/
│   ├── nodes.yaml.example          # Шаблон инвентаря нод
│   ├── global.yaml.example         # Шаблон глобальных настроек
│   ├── secrets/                    # Секреты нод (автогенерация)
│   └── templates/                  # Jinja2-шаблоны конфигов
├── core/
│   ├── node_manager.py             # Инвентарь нод + SSH (Paramiko)
│   ├── deployer.py                 # Удалённый деплой + генерация секретов
│   ├── config_gen.py               # Рендеринг шаблонов + URI подписок
│   ├── bridge_manager.py           # Управление bridge-конфигами + failover
│   ├── monitor.py                  # Асинхронные проверки + триггер failover
│   ├── telegram.py                 # Обёртка Telegram Bot API (aiogram)
│   ├── bot/                        # Telegram-бот (aiogram 3, inline-клавиатуры)
│   │   ├── __init__.py             #   Сборка BulwarkBot, роутинг, запуск
│   │   ├── _base.py                #   Базовый класс: init, хелперы
│   │   ├── _helpers.py             #   Утилиты, FSM states, middleware
│   │   ├── _status.py              #   Статус нод, рестарт, логи, sysinfo
│   │   ├── _traffic.py             #   Сбор трафика (SSH / vnstat)
│   │   ├── _subs.py                #   Подписки VLESS / HY2
│   │   ├── _mgmt.py                #   Управление, портал, failover, обновления
│   │   ├── _nodes.py               #   Wizard добавления нод (FSM)
│   │   ├── _awg.py                 #   AmneziaWG: пиры и ссылки
│   │   └── _loops.py               #   Фоновые циклы (дайджест, алерты)
│   ├── stats.py                    # SQLite-статистика
│   └── awg_users.py                # Управление AWG API аккаунтами
├── deploy/
│   ├── scripts/                    # Bash-скрипты установки
│   └── portal/
│       └── app.py                  # Flask API подписок
└── LICENSE
```

## Документация

- [Руководство администратора](docs/admin-guide.md) — полный цикл: настройка, добавление нод, деплой, мониторинг, обслуживание, диагностика

## Лицензия

Проект распространяется по лицензии [GNU General Public License v3.0](LICENSE).

## Связанные проекты

- [Xray-core](https://github.com/XTLS/Xray-core) — движок протоколов VLESS+Reality (MPL 2.0)
- [Hysteria](https://github.com/apernet/hysteria) — протокол Hysteria2 (MIT)
- [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module) — модуль ядра AmneziaWG (GPL 2.0)
- [amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) — userspace-утилиты AmneziaWG (MIT)
