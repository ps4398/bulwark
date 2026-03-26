#!/usr/bin/env bash
# Bulwark — install / upgrade AmneziaWG 2.0
# * Kernel module: built from source (amneziawg-linux-kernel-module)
# * Userspace tools (awg, awg-quick): built from source (amneziawg-tools)
#   Pre-built binaries from GitHub releases do NOT support i1-i5 (Signature Packets).
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

AWG_DIR="/etc/amnezia/amneziawg"

echo "[bulwark-awg] Installing AmneziaWG (from source)..."

# ---------------------------------------------------------------------------
# Stop service before upgrade
# ---------------------------------------------------------------------------
systemctl stop wg-quick@awg0 2>/dev/null || true

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
apt-get install -y -qq \
    wireguard-tools iproute2 iptables curl git \
    linux-headers-"$(uname -r)" build-essential pkg-config

# ---------------------------------------------------------------------------
# Kernel module — build from source (src/ subdirectory of the repo)
# Pre-built binaries don't exist for kernel modules (kernel-version specific).
# ---------------------------------------------------------------------------
echo "[bulwark-awg] Building amneziawg kernel module from source..."
TMP_KM=$(mktemp -d)
git clone --depth=1 https://github.com/amnezia-vpn/amneziawg-linux-kernel-module.git "${TMP_KM}/awg-km"

KERNEL_VER="$(uname -r)"
KERNEL_BUILD="/lib/modules/${KERNEL_VER}/build"

make -C "${KERNEL_BUILD}" M="${TMP_KM}/awg-km/src" modules

# Install into updates/ so it takes priority over any PPA-installed module
INSTALL_DIR="/lib/modules/${KERNEL_VER}/updates"
mkdir -p "${INSTALL_DIR}"
install -m 644 "${TMP_KM}/awg-km/src/amneziawg.ko" "${INSTALL_DIR}/amneziawg.ko"
depmod -a "${KERNEL_VER}"
rm -rf "${TMP_KM}"
echo "[bulwark-awg] Kernel module installed."

# ---------------------------------------------------------------------------
# Userspace tools — build from source (pre-built binaries lack i1-i5 support)
# ---------------------------------------------------------------------------
echo "[bulwark-awg] Building amneziawg-tools from source..."
TMP_TOOLS=$(mktemp -d)
git clone --depth=1 https://github.com/amnezia-vpn/amneziawg-tools.git "${TMP_TOOLS}/tools"

make -C "${TMP_TOOLS}/tools/src" -j"$(nproc)"

# Build produces 'wg' binary — install as 'awg'
install -m 755 "${TMP_TOOLS}/tools/src/wg" /usr/local/bin/awg
install -m 755 "${TMP_TOOLS}/tools/src/wg-quick/linux.bash" /usr/local/bin/awg-quick

# Override PPA-installed old binaries
ln -sf /usr/local/bin/awg       /usr/bin/awg
ln -sf /usr/local/bin/awg-quick /usr/bin/awg-quick

rm -rf "${TMP_TOOLS}"
echo "[bulwark-awg] Tools built and installed."

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
echo "[bulwark-awg] awg version: $(awg --version 2>&1)"

# ---------------------------------------------------------------------------
# Reload kernel module
# ---------------------------------------------------------------------------
modprobe -r amneziawg 2>/dev/null || true
modprobe amneziawg
echo "[bulwark-awg] Kernel module: $(lsmod | grep amneziawg | head -1 || echo 'check manually')"

# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------
mkdir -p "${AWG_DIR}"
chmod 700 "${AWG_DIR}"

# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/wg-quick@awg0.service <<'SERVICE'
[Unit]
Description=AmneziaWG (awg-quick) — %i (Bulwark)
After=network.target nss-lookup.target ufw.service
ConditionPathExists=/etc/amnezia/amneziawg/%i.conf

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/env WG_QUICK_USERSPACE_IMPLEMENTATION=awg awg-quick up %i
ExecStop=/usr/bin/env WG_QUICK_USERSPACE_IMPLEMENTATION=awg awg-quick down %i
ExecReload=/bin/kill -s HUP $MAINPID
Environment=WG_ENDPOINT_RESOLUTION_RETRIES=infinity

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable wg-quick@awg0

# Placeholder config (deployer overwrites this)
if [ ! -f "${AWG_DIR}/awg0.conf" ]; then
    cat > "${AWG_DIR}/awg0.conf" <<'CONF'
# Placeholder — will be replaced by Bulwark deployer
[Interface]
PrivateKey = PLACEHOLDER
Address = 10.0.0.1/24
ListenPort = 51820
CONF
    chmod 600 "${AWG_DIR}/awg0.conf"
fi

echo "[bulwark-awg] AmneziaWG installed. Version: $(awg --version 2>&1)"
