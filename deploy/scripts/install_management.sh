#!/usr/bin/env bash
# Bulwark — install management system on a remote node
# Installs Python venv, creates /opt/bulwark, registers bulwark-monitor systemd service.
# Run by the deployer after uploading the project files.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
BW_DIR="/opt/bulwark"
VENV_DIR="${BW_DIR}/venv"
BIN_LINK="/usr/local/bin/bulwark"
SYSTEMD_UNIT="/etc/systemd/system/bulwark-monitor.service"

echo "[bulwark] Installing Python dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv

echo "[bulwark] Creating virtualenv at ${VENV_DIR}..."
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${BW_DIR}/requirements.txt"

echo "[bulwark] Creating CLI wrapper at ${BIN_LINK}..."
cat > "${BIN_LINK}" <<WRAPPER
#!/usr/bin/env bash
exec "${VENV_DIR}/bin/python" "${BW_DIR}/cli.py" "\$@"
WRAPPER
chmod +x "${BIN_LINK}"

echo "[bulwark] Installing bulwark-monitor systemd service..."
cat > "${SYSTEMD_UNIT}" <<'SERVICE'
[Unit]
Description=Bulwark Monitor Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bulwark
EnvironmentFile=/opt/bulwark/.env
ExecStart=/opt/bulwark/venv/bin/python /opt/bulwark/cli.py monitor
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable bulwark-monitor

echo "[bulwark] Bulwark management system installed."
echo "[bulwark] Run 'bulwark status' to verify."
echo "[bulwark] Start monitor with: systemctl start bulwark-monitor"
