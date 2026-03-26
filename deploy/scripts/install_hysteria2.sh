#!/usr/bin/env bash
# Bulwark — install Hysteria2 (latest) with self-signed TLS cert
set -euo pipefail

INSTALL_DIR="/usr/local/bin"
HYSTERIA_BIN="${INSTALL_DIR}/hysteria"
CONFIG_DIR="/etc/hysteria"
LOG_DIR="/var/log/hysteria2"
CERT_FILE="${CONFIG_DIR}/server.crt"
KEY_FILE="${CONFIG_DIR}/server.key"
SYSTEMD_UNIT="/etc/systemd/system/hysteria2.service"
ARCH=$(uname -m)
TMP_DIR=$(mktemp -d)

echo "[bulwark-hy2] Installing Hysteria2..."

# ---------------------------------------------------------------------------
# Determine download architecture
# ---------------------------------------------------------------------------
case "${ARCH}" in
    x86_64)  HY2_ARCH="amd64" ;;
    aarch64) HY2_ARCH="arm64" ;;
    armv7l)  HY2_ARCH="armv7" ;;
    *)
        echo "[bulwark-hy2] ERROR: Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Resolve latest release tag
# ---------------------------------------------------------------------------
echo "[bulwark-hy2] Resolving latest Hysteria2 release..."
LATEST_TAG=$(curl -fsSL "https://api.github.com/repos/apernet/hysteria/releases/latest" \
    | grep '"tag_name"' \
    | sed -E 's/.*"([^"]+)".*/\1/')

if [ -z "${LATEST_TAG}" ]; then
    echo "[bulwark-hy2] WARNING: Could not resolve latest tag, using fallback."
    LATEST_TAG="app/v2.4.5"
fi

echo "[bulwark-hy2] Latest tag: ${LATEST_TAG}"

# Strip "app/" prefix if present for the version string
VERSION="${LATEST_TAG#app/}"

DOWNLOAD_URL="https://github.com/apernet/hysteria/releases/download/${LATEST_TAG}/hysteria-linux-${HY2_ARCH}"

echo "[bulwark-hy2] Downloading from ${DOWNLOAD_URL} ..."
curl -fsSL -o "${TMP_DIR}/hysteria" "${DOWNLOAD_URL}"
chmod +x "${TMP_DIR}/hysteria"
install -m 755 "${TMP_DIR}/hysteria" "${HYSTERIA_BIN}"

echo "[bulwark-hy2] Installed: $("${HYSTERIA_BIN}" version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"
chmod 700 "${CONFIG_DIR}"
chmod 755 "${LOG_DIR}"

# ---------------------------------------------------------------------------
# Self-signed TLS certificate (1-year validity)
# ---------------------------------------------------------------------------
if [ ! -f "${CERT_FILE}" ] || [ ! -f "${KEY_FILE}" ]; then
    echo "[bulwark-hy2] Generating self-signed TLS certificate..."
    SERVER_IP=$(hostname -I | awk '{print $1}')
    openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
        -keyout "${KEY_FILE}" \
        -out "${CERT_FILE}" \
        -days 365 \
        -subj "/CN=${SERVER_IP}" \
        -addext "subjectAltName=IP:${SERVER_IP}" 2>/dev/null

    chmod 600 "${KEY_FILE}"
    chmod 644 "${CERT_FILE}"
    echo "[bulwark-hy2] TLS certificate generated for IP: ${SERVER_IP}"
else
    echo "[bulwark-hy2] TLS certificate already exists, skipping generation."
fi

# ---------------------------------------------------------------------------
# Placeholder config (will be replaced by deployer)
# ---------------------------------------------------------------------------
if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    cat > "${CONFIG_DIR}/config.yaml" <<'YAML'
# Placeholder — will be replaced by Bulwark deployer
listen: ":8443"
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
YAML
fi

# ---------------------------------------------------------------------------
# systemd service
# ---------------------------------------------------------------------------
echo "[bulwark-hy2] Installing systemd service..."
cat > "${SYSTEMD_UNIT}" <<'SERVICE'
[Unit]
Description=Hysteria2 Server (Bulwark)
Documentation=https://hysteria.network/
After=network.target

[Service]
User=root
ExecStart=/usr/local/bin/hysteria server --config /etc/hysteria/config.yaml
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5s
LimitNOFILE=65535
LimitNPROC=512

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable hysteria2

# Clean up
rm -rf "${TMP_DIR}"

echo "[bulwark-hy2] Hysteria2 ${VERSION} installed successfully."
