#!/usr/bin/env bash
# Bulwark — install xray-core v1.8.4
set -euo pipefail

XRAY_VERSION="${XRAY_VERSION:-1.8.4}"
INSTALL_DIR="/usr/local/bin"
XRAY_BIN="${INSTALL_DIR}/xray"
LOG_DIR="/var/log/xray"
CONFIG_DIR="/usr/local/etc/xray"
SYSTEMD_UNIT="/etc/systemd/system/xray.service"
ARCH=$(uname -m)
TMP_DIR=$(mktemp -d)

echo "[bulwark-xray] Installing xray-core v${XRAY_VERSION}..."

# ---------------------------------------------------------------------------
# Determine download URL by architecture
# ---------------------------------------------------------------------------
case "${ARCH}" in
    x86_64)   XRAY_ARCH="64"   ;;
    aarch64)  XRAY_ARCH="arm64-v8a" ;;
    armv7l)   XRAY_ARCH="arm32-v7a" ;;
    *)
        echo "[bulwark-xray] ERROR: Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

DOWNLOAD_URL="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"

echo "[bulwark-xray] Downloading from ${DOWNLOAD_URL} ..."
curl -fsSL -o "${TMP_DIR}/xray.zip" "${DOWNLOAD_URL}"

# ---------------------------------------------------------------------------
# Extract and install
# ---------------------------------------------------------------------------
echo "[bulwark-xray] Extracting..."
unzip -q "${TMP_DIR}/xray.zip" -d "${TMP_DIR}/xray_extracted"

install -m 755 "${TMP_DIR}/xray_extracted/xray" "${XRAY_BIN}"

# Ensure data directory exists before attempting to install files into it
mkdir -p /usr/local/share/xray

# Install geoip and geosite data files
for dat_file in geoip.dat geosite.dat; do
    if [ -f "${TMP_DIR}/xray_extracted/${dat_file}" ]; then
        install -m 644 "${TMP_DIR}/xray_extracted/${dat_file}" "/usr/local/share/xray/${dat_file}"
    fi
done

# Download latest geoip/geosite if not bundled
if [ ! -f "/usr/local/share/xray/geoip.dat" ]; then
    curl -fsSL -o "/usr/local/share/xray/geoip.dat" \
        "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat" || true
fi
if [ ! -f "/usr/local/share/xray/geosite.dat" ]; then
    curl -fsSL -o "/usr/local/share/xray/geosite.dat" \
        "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat" || true
fi

echo "[bulwark-xray] Installed: $("${XRAY_BIN}" version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------
mkdir -p "${LOG_DIR}"
chmod 755 "${LOG_DIR}"

# ---------------------------------------------------------------------------
# systemd service
# ---------------------------------------------------------------------------
echo "[bulwark-xray] Installing systemd service..."
# Remove any existing Drop-In overrides that may redirect xray to a different
# config path (e.g. from a previous official-installer xray installation).
if [ -d "/etc/systemd/system/xray.service.d" ]; then
    echo "[bulwark-xray] Removing existing xray service Drop-Ins..."
    rm -rf /etc/systemd/system/xray.service.d
fi
cat > "${SYSTEMD_UNIT}" <<'SERVICE'
[Unit]
Description=Xray Service (Bulwark)
Documentation=https://github.com/xtls/xray-core
After=network.target nss-lookup.target

[Service]
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
NoNewPrivileges=true
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartPreventExitStatus=23
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable xray

# ---------------------------------------------------------------------------
# Placeholder config (will be replaced by deployer)
# ---------------------------------------------------------------------------
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/config.json" ]; then
    cat > "${CONFIG_DIR}/config.json" <<'JSON'
{
  "log": { "loglevel": "warning" },
  "inbounds": [],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}]
}
JSON
fi

# Clean up
rm -rf "${TMP_DIR}"

echo "[bulwark-xray] xray-core v${XRAY_VERSION} installed successfully."
