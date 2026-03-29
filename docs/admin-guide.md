# Руководство администратора Bulwark

## Содержание

- [Общая схема работы](#общая-схема-работы)
- [Первоначальная настройка](#первоначальная-настройка)
- [Добавление нод](#добавление-нод)
- [Деплой протоколов](#деплой-протоколов)
- [Bridge-маршрутизация](#bridge-маршрутизация)
- [Мониторинг и failover](#мониторинг-и-failover)
- [Подписки](#подписки)
- [AmneziaWG: управление пирами](#amneziawg-управление-пирами)
- [Портал подписок](#портал-подписок)
- [Telegram-бот: справочник](#telegram-бот-справочник)
- [Обслуживание](#обслуживание)
- [Конфигурационные файлы](#конфигурационные-файлы)
- [Диагностика проблем](#диагностика-проблем)
- [Краткий справочник команд](#краткий-справочник-команд)

---

## Общая схема работы

```
1. setup.sh на VPS        →  Management-бридж готов
2. systemctl start bot    →  Бот доступен в Telegram
3. Wizard в боте          →  Добавить exit-ноды
4. Деплой через бот       →  Протоколы установлены
5. bridge update          →  Маршрутизация настроена
6. sub push               →  Подписка опубликована
7. Мониторинг             →  Автоматический failover
```

**Роли нод:**

| Роль | Что делает | Протоколы |
|------|-----------|-----------|
| **bridge** | Relay-проксирование трафика к exit-нодам через xray | Нет (только xray relay) |
| **exit** | Конечная точка, принимает и обрабатывает трафик | VLESS+Reality, Hysteria2, AmneziaWG |

---

## Первоначальная настройка

Выполняется один раз на сервере, который станет management-бриджем.

```bash
wget -qO- https://github.com/ps4398/bulwark/archive/refs/heads/main.tar.gz | tar xz
cd bulwark-main
chmod +x setup.sh
sudo ./setup.sh
```

Скрипт запросит:

| Параметр | Описание | Где взять |
|----------|----------|-----------|
| Telegram Bot Token | Токен бота | [@BotFather](https://t.me/BotFather) → `/newbot` |
| Telegram Chat ID | ID чата с ботом | Отправить `/start` боту, затем `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| Telegram User ID | Ваш ID в Telegram | [@userinfobot](https://t.me/userinfobot) |
| SSH-пароль | Пароль текущего сервера | Для self-management через бот |
| Reality SNI | Домен для TLS-маскировки (должен быть доступен с IP сервера) | Например `google.com` |
| Имя ноды | Идентификатор (default: `management`) | Латиница, snake_case |
| Регион | Код страны (default: `ru`) | `ru`, `fi`, `at`, `de`, `nl`, `us`... |

IP сервера определяется автоматически.

**Что делает setup.sh:**

1. Генерирует `.env`, `config/nodes.yaml`, `config/global.yaml`
2. Создаёт Python virtualenv, устанавливает зависимости
3. Запускает `install_base.sh` — firewall (UFW), sysctl (IP forwarding, BBR), fail2ban
4. Запускает `install_xray.sh` — бинарник xray-core для bridge relay
5. Генерирует секреты ноды (X25519, UUID)
6. Регистрирует systemd-сервис `bulwark-bot`

**После setup:**

```bash
systemctl start bulwark-bot
journalctl -u bulwark-bot -f    # логи бота
```

---

## Добавление нод

### Через Telegram-бот (рекомендуется)

В боте: **⚙️ Управление → ➕ Добавить ноду**

Wizard проведёт через шаги:

**Для exit-ноды:**
1. Тип → Exit
2. Имя (snake_case, 3-31 символ, уникальное)
3. IP-адрес (IPv4)
4. Регион (2-5 символов)
5. Приоритет: 1 = основная, 2 = резервная (для failover)
6. SSH-аутентификация: пароль или путь к ключу
7. Протоколы: VLESS+Reality / Hysteria2 / AmneziaWG (переключаемые)
8. Подтверждение → автоматический деплой

**Для bridge-ноды:**
1. Тип → Bridge
2. Имя, IP, регион
3. SSH-аутентификация
4. Режим: multi-port или single-port (443, UUID-маршрутизация)
5. Подтверждение → деплой base + xray binary + push bridge config

### Через CLI

```bash
# Вручную добавить в config/nodes.yaml, затем:
python cli.py secrets regenerate <имя_ноды>
python cli.py deploy <имя_ноды>
python cli.py bridge update
```

> **SSH-аутентификация:** если нода использует пароль — добавьте переменную в `.env` (например `MY_NODE_SSH_PASSWORD=...`), а в `nodes.yaml` укажите `ssh_password_env: "MY_NODE_SSH_PASSWORD"`. Для ключа — `ssh_key_path: "config/secrets/my_key"`.

---

## Деплой протоколов

```bash
python cli.py deploy <нода> [--protocol xray|hy2|awg|all]
```

**Порядок установки на exit-ноду:**

| Шаг | Что происходит | Скрипт |
|-----|---------------|--------|
| 1 | Подготовка системы (firewall, sysctl, dirs) | `install_base.sh` |
| 2 | Установка xray-core | `install_xray.sh` |
| 3 | Генерация секретов, рендер конфига, загрузка, рестарт | — |
| 4 | Установка Hysteria2 + self-signed TLS-сертификат (P-256, 1 год, `openssl` на ноде) | `install_hysteria2.sh` |
| 5 | Генерация конфига, загрузка, рестарт | — |
| 6 | Сборка AmneziaWG из исходников (kernel module + tools) | `install_amneziawg.sh` |
| 7 | Генерация ключей, конфига, загрузка, рестарт | — |
| 8 | Обновление bridge-маршрутизации на всех бриджах | — |

**Передеплой (только конфиги, без переустановки бинарников):**

```bash
python cli.py redeploy <нода> <протокол>
```

**Предварительный просмотр конфига:**

```bash
python cli.py config show <нода> <протокол>    # показать что будет сгенерировано
python cli.py config sync <нода>                # загрузить как *.pending (без рестарта)
```

---

## Bridge-маршрутизация

Бриджи — relay-прокси на xray. Каждый бридж знает обо всех активных exit-нодах и маршрутизирует трафик к ним.

**Режимы:**

| Режим | Порт | Маршрутизация | Когда использовать |
|-------|------|--------------|-------------------|
| Multi-port | по порту (один порт = одна exit-нода) | Рандомный диапазон при setup | По умолчанию |
| Single-port | 443 | По UUID в VLESS-соединении | Когда нужен единый порт |

**Команды:**

```bash
python cli.py bridge update     # перегенерировать и загрузить конфиг на все бриджи
python cli.py bridge routes     # показать активные маршруты
python cli.py bridge deploy <нода>  # развернуть новый бридж
```

**Когда нужно делать `bridge update`:**
- После добавления/удаления exit-ноды
- После включения/отключения ноды
- После failover/failback (делается автоматически)
- В боте: **⚙️ Управление → 🔄 Bridge Push**

---

## Мониторинг и failover

Мониторинг запускается автоматически вместе с ботом.

**Параметры** (`config/global.yaml`):

```yaml
monitoring:
  interval: 30          # проверка каждые 30 секунд
  failover_threshold: 3 # 3 подряд неудачи → failover
  icmp_timeout: 5
  tcp_timeout: 5
```

**Что проверяется:**

| Проверка | Порт | Метод |
|----------|------|-------|
| Доступность | — | ICMP ping |
| VLESS+Reality | 443/tcp | TCP connect |
| Hysteria2 | 8443/udp | UDP probe |

**Алгоритм failover:**

```
Нода недоступна (1 раз)  →  Telegram-алерт 🔴
Нода недоступна (3 раза)  →  Failover:
    1. Нода исключается из bridge-маршрутов
    2. Резервная нода (priority=2) активируется
    3. Bridge-конфиг перегенерируется и загружается
    4. Telegram-алерт ⚡
Нода восстановилась       →  Failback:
    1. Нода возвращается в маршруты
    2. Bridge-конфиг обновляется
    3. Telegram-алерт ✅
```

**Ручной failover** (в боте): **⚙️ Управление → ⚡ Failover**

---

## Подписки

Подписка — base64-закодированный список URI (VLESS + Hysteria2) для импорта в прокси-приложения (V2Ray, Nekobox, Hiddify и т.д.).

**Генерация и публикация:**

```bash
python cli.py sub show       # показать URI в читаемом виде
python cli.py sub generate   # сгенерировать base64
python cli.py sub push       # загрузить на портал
```

В боте: **⚙️ Управление → 📤 Sub Push**

**Что генерируется при `sub push`:**

| Файл | Назначение | Путь на портале |
|------|-----------|----------------|
| `subscription.b64` | V2Ray подписка | `/opt/bulwark/portal/runtime/` |
| `connections.json` | URI с метаданными | `/opt/bulwark/portal/runtime/` |
| `awg_info.json` | AWG-параметры и AWG 2.0 ссылки | `/opt/bulwark/portal/runtime/` |

**Состав подписки** (для каждой exit-ноды):
- VLESS+Reality (прямое подключение)
- VLESS+Reality через каждый bridge
- Hysteria2 (прямое подключение)

---

## AmneziaWG: управление пирами

Каждый пир — это конфигурация устройства с уникальным ключом и IP-адресом.

**Добавление пира:**

```bash
# Генерация ключей на сервере (для собственных устройств)
python cli.py awg add-peer <нода> --name my-phone

# С указанием публичного ключа (устройство сгенерировало ключи само)
python cli.py awg add-peer <нода> --pubkey <ключ>
```

В боте: **🛡 AmneziaWG → <нода> → ➕ Добавить пира**

**Что происходит при добавлении:**

1. Генерация ключей на удалённом сервере (`awg genkey` / `wg genkey`)
2. Выделение IP: `10.<region_octet>.0.<N>/32` (следующий свободный)
3. Обновление `config/secrets/<нода>.yaml` (локально)
4. Перегенерация конфига AWG, загрузка на сервер
5. `systemctl restart wg-quick@awg0`
6. Генерация AWG 2.0 ссылки для импорта в AmneziaVPN

**AWG 2.0 ссылки:**
- Содержат: приватный ключ, публичный ключ сервера, endpoint, обфускация
- Импортируются в приложение AmneziaVPN
- Можно генерировать через разные маршруты (Direct / через каждый bridge)

**Просмотр и генерация ссылок:**

```bash
python cli.py awg list-peers <нода>                    # список пиров
python cli.py awg gen-link <нода> --pubkey <ключ>      # AWG 2.0 ссылка
```

В боте: **🛡 AmneziaWG → <нода> → <пир> → выбрать маршрут**

**AWG relay через бриджи:**

AmneziaWG — UDP-протокол. Для relay через бриджи используется xray dokodemo-door (UDP forwarding):
- Multi-port bridge: UDP порты (awg_relay_port_start из global.yaml)
- Single-port bridge: UDP порты (awg_relay_port_start_single из global.yaml)

---

## Портал подписок

Опциональный Flask-сервис для раздачи подписок и AWG auto-provisioning.

**Развёртывание:**

```bash
python cli.py management install <bridge_нода>
# Устанавливает /opt/bulwark/ на удалённый сервер
# Регистрирует systemd-сервис bulwark-monitor
```

Портал слушает `127.0.0.1:8787`. Для публичного доступа — reverse proxy (nginx / Cloudflare Tunnel).

**Эндпоинты:**

| Путь | Метод | Описание |
|------|-------|----------|
| `/<sub_prefix>/<token>` | GET | V2Ray подписка (base64) |
| `/<awg_prefix>/` | POST | AWG auto-provisioning |
| `/<awg_prefix>/<нода>/` | POST | То же, на конкретную ноду |

> Префиксы `<sub_prefix>` и `<awg_prefix>` генерируются случайно при `setup.sh` и хранятся в `.env` (`PORTAL_SUB_PREFIX`, `PORTAL_AWG_PREFIX`).

**Обслуживание портала:**

```bash
# Проверка доступности (в боте): ⚙️ Управление → 🌐 Портал
# Reload Gunicorn (в боте): ⚙️ Управление → 🔄 Портал Reload
```

---

## Telegram-бот: справочник

Бот доступен только администратору (ID задаётся при настройке).

### Главное меню

```
📊 Статус нод    📈 Трафик
🔗 Подписки      🛡 AmneziaWG
⚙️ Управление    ➕ Добавить ноду
```

### Экраны и действия

**📊 Статус нод**
- Сводка: имя, роль, регион, ICMP, статус протоколов
- Клик по ноде → детали + кнопки:
  - 🔄 Рестарт сервисов (xray / hy2 / awg)
  - 📋 Логи (journalctl последние 30 строк)
  - 💻 Sysinfo (CPU / RAM / Disk / Uptime)
  - 📊 Статистика (uptime%, инциденты из SQLite)
  - 📡 Speedtest
  - 🧹 Очистка логов
  - 🔌 Reboot (с подтверждением)

**📈 Трафик**
- Трафик по каждой ноде (vnstat / /proc/net/dev)

**🔗 Подписки**
- Выбор exit-ноды → список URI (VLESS direct, VLESS via bridge, HY2)
- Полная ссылка на V2Ray подписку

**🛡 AmneziaWG**
- Выбор exit-ноды → список пиров
- Выбор пира → выбор маршрута (Direct / через каждый bridge)
- Генерация AWG 2.0 ссылки
- ➕ Добавить пира

**⚙️ Управление**
- 🔄 Bridge Push — перегенерация bridge-конфигов
- 📤 Sub Push — обновление подписки на портале
- 🌐 Портал — проверка доступности
- 🔄 Портал Reload — `kill -HUP gunicorn`
- 🔧 Обновления — проверка версий xray/hysteria2
- ⚡ Failover — ручное переключение
- 🔀 Вкл/Выкл нод — включение/отключение без удаления

---

## Обслуживание

### Обновление бинарников

В боте: **⚙️ Управление → 🔧 Обновления → <нода>**

Показывает текущую vs последнюю версию (GitHub API, кеш 30 мин). Обновление — нажатием кнопки.

Через CLI:
```bash
python cli.py deploy <нода> --protocol xray       # переустановит xray
python cli.py deploy <нода> --protocol hysteria2   # переустановит hy2
```

### Очистка логов

В боте: **📊 Статус → <нода> → 🧹 Очистка логов**

Выполняет: `journalctl --vacuum-time=7d --vacuum-size=100M`

### Включение/отключение нод

В боте: **⚙️ Управление → 🔀 Вкл/Выкл**

Сохраняется в `config/node_overrides.json`. Bridge-конфиг перегенерируется автоматически. Не удаляет ноду из `nodes.yaml`.

### Бэкап секретов

```bash
# Скопируйте директорию секретов в безопасное место
cp -r config/secrets/ /path/to/backup/
```

> Без секретов невозможно восстановить конфигурацию нод. При потере — потребуется полный передеплой.

---

## Конфигурационные файлы

### `.env`

```bash
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
MANAGEMENT_SSH_PASSWORD=<your-password>
# Добавляйте пароли нод по мере их создания:
# MY_EXIT_NODE_SSH_PASSWORD=...
```

### `config/nodes.yaml`

```yaml
nodes:
  management:
    name: management
    display_name: "Management Bridge"
    ip: "203.0.113.1"
    ssh_port: 22
    ssh_user: root
    ssh_password_env: "MANAGEMENT_SSH_PASSWORD"
    role: bridge
    region: de
    priority: 1
    enabled: true
    protocols: []
    inbound_port_start: 21537    # рандомизируется при setup
```

### `config/global.yaml`

Основные секции: `monitoring`, `telegram`, `software`, `amneziawg`, `ports`, `bridge`, `xray`, `hysteria2`, `amneziawg_server`, `logging`. Подробная структура — в `config/global.yaml.example`.

### `config/secrets/<нода>.yaml`

Генерируется автоматически. Содержит: UUID, X25519 ключи Reality, WireGuard ключи, параметры обфускации AWG, список AWG-пиров.

---

## Диагностика проблем

### Нода показывается offline

```bash
# 1. Проверить связь
ping <ip>
nc -zv <ip> 443        # VLESS

# 2. Проверить сервисы на ноде
ssh root@<ip> systemctl status xray
ssh root@<ip> systemctl status hysteria2
ssh root@<ip> systemctl status wg-quick@awg0

# 3. Проверить firewall
ssh root@<ip> ufw status

# 4. Логи
ssh root@<ip> journalctl -u xray -n 50 --no-pager
```

### Bridge-конфиг не проходит валидацию

```bash
# Посмотреть сгенерированный конфиг
python cli.py config show <bridge> --protocol xray

# Проверить на ноде
ssh root@<bridge_ip> /usr/local/bin/xray -test -config /usr/local/etc/xray/config.json
ssh root@<bridge_ip> journalctl -u xray -n 50
```

### Подписка не обновляется на портале

```bash
# Проверить файлы на портале
ssh root@<bridge_ip> ls -la /opt/bulwark/portal/runtime/

# Вручную загрузить
python cli.py sub push

# Проверить ответ портала
ssh root@<bridge_ip> curl -s http://localhost:8787/sub/<TOKEN> | head -c 100
```

### AWG-пир не подключается

```bash
# Проверить сервис
ssh root@<ip> systemctl status wg-quick@awg0
ssh root@<ip> awg show

# Проверить конфиг
ssh root@<ip> cat /etc/amnezia/amneziawg/awg0.conf

# Проверить NAT
ssh root@<ip> iptables -t nat -S POSTROUTING | grep MASQUERADE
```

### Бот не отвечает

```bash
journalctl -u bulwark-bot -n 100 --no-pager
systemctl restart bulwark-bot
```

---

## Краткий справочник команд

**Статус:**
```bash
python cli.py status [нода]
python cli.py bridge routes
```

**Деплой:**
```bash
python cli.py deploy <нода> [--protocol xray|hy2|awg|all]
python cli.py redeploy <нода> <протокол>
python cli.py bridge deploy <нода>
```

**Конфигурация:**
```bash
python cli.py config show <нода> <протокол>
python cli.py config sync <нода>
python cli.py bridge update
python cli.py secrets show <нода>
python cli.py secrets regenerate <нода>
```

**Подписки:**
```bash
python cli.py sub show
python cli.py sub generate
python cli.py sub push
```

**AmneziaWG:**
```bash
python cli.py awg add-peer <нода> [--name имя] [--pubkey ключ]
python cli.py awg list-peers <нода>
python cli.py awg gen-link <нода> --pubkey <ключ>
```

**Управление:**
```bash
python cli.py management install <нода>
python cli.py management status <нода>
python cli.py bot start [--no-monitor]
python cli.py node list
python cli.py monitor
```
