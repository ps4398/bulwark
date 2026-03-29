#!/usr/bin/env bash
# Bulwark — base system preparation
# Run once on a fresh Ubuntu/Debian VPS before deploying any protocol.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

echo "[bulwark-base] Updating package lists..."
apt-get update -qq

echo "[bulwark-base] Upgrading installed packages..."
apt-get upgrade -y -qq

echo "[bulwark-base] Installing required packages..."
apt-get -f install -y -qq 2>/dev/null || true
apt-get install -y -qq \
    curl \
    wget \
    git \
    unzip \
    jq \
    openssl \
    iptables \
    ufw \
    fail2ban \
    net-tools \
    htop \
    lsof \
    ca-certificates \
    gnupg \
    lsb-release

# iptables-persistent is optional — not available on nftables-based systems
apt-get install -y -qq iptables-persistent netfilter-persistent 2>/dev/null || \
    echo "[bulwark-base] iptables-persistent not available (nftables system), skipping."

# ---------------------------------------------------------------------------
# Create required directories
# ---------------------------------------------------------------------------
echo "[bulwark-base] Creating service directories..."
mkdir -p /etc/xray
mkdir -p /var/log/xray
mkdir -p /etc/hysteria
mkdir -p /var/log/hysteria2
mkdir -p /etc/amneziawg

chmod 700 /etc/xray
chmod 700 /etc/hysteria
chmod 700 /etc/amneziawg

# ---------------------------------------------------------------------------
# IP forwarding
# ---------------------------------------------------------------------------
echo "[bulwark-base] Enabling IP forwarding..."
cat > /etc/sysctl.d/99-bulwark-forward.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
# BBR congestion control
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
SYSCTL

sysctl --system -q

# ---------------------------------------------------------------------------
# UFW firewall rules
# ---------------------------------------------------------------------------
echo "[bulwark-base] Configuring UFW firewall..."

# Locate ufw binary (may be in /usr/sbin on some systems)
UFW=$(command -v ufw 2>/dev/null || echo /usr/sbin/ufw)
if [ ! -x "${UFW}" ]; then
    echo "[bulwark-base] WARNING: ufw not found at ${UFW}, skipping firewall config."
else
    # Allow IP forwarding through UFW chains — required for AmneziaWG NAT (MASQUERADE).
    # Without this, UFW's default DROP policy on the FORWARD chain blocks forwarded traffic
    # even when iptables PostUp rules are active.
    sed -i 's/^DEFAULT_FORWARD_POLICY="DROP"/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw 2>/dev/null || true

    # Ports configurable via env vars (set by deployer), fallback to defaults
    VLESS_PORT="${VLESS_PORT:-443}"
    HY2_PORT="${HY2_PORT:-8443}"
    AWG_PORT="${AWG_PORT:-51820}"
    BRIDGE_PORT_START="${BRIDGE_PORT_START:-0}"
    BRIDGE_PORT_END="${BRIDGE_PORT_END:-0}"

    "${UFW}" allow 22/tcp comment "SSH"
    "${UFW}" allow "${VLESS_PORT}"/tcp comment "VLESS-Reality"
    "${UFW}" allow "${HY2_PORT}"/udp comment "Hysteria2"
    "${UFW}" allow "${AWG_PORT}"/udp comment "AmneziaWG"
    if [ "${BRIDGE_PORT_START}" -gt 0 ] && [ "${BRIDGE_PORT_END}" -gt 0 ]; then
        "${UFW}" allow "${BRIDGE_PORT_START}":"${BRIDGE_PORT_END}"/tcp comment "Bridge relay"
    fi

    # Allow all outbound
    "${UFW}" default allow outgoing
    "${UFW}" default deny incoming

    # Enable without interactive prompt
    "${UFW}" --force enable

    echo "[bulwark-base] UFW status:"
    "${UFW}" status verbose
fi

# ---------------------------------------------------------------------------
# iptables persistent save
# ---------------------------------------------------------------------------
netfilter-persistent save 2>/dev/null || true

# ---------------------------------------------------------------------------
# fail2ban for SSH protection
# ---------------------------------------------------------------------------
echo "[bulwark-base] Configuring fail2ban..."
cat > /etc/fail2ban/jail.local <<'F2B'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port    = ssh
logpath = %(sshd_log)s
backend = %(syslog_backend)s
F2B

systemctl enable fail2ban
systemctl restart fail2ban

echo "[bulwark-base] Base installation complete."
