#!/bin/bash

# FOSS SOC - Service Installer
# Usage: sudo ./setup_service.sh

# 1. Check for Sudo/Root
if [ "$EUID" -ne 0 ]; then 
  echo "❌ Please run as root (use sudo)"
  exit 1
fi

# 2. Detect Configuration
SERVICE_NAME="foss-soc"
CURRENT_USER=$(logname)
CURRENT_DIR=$(pwd)
PYTHON_EXEC=$(which python3)

echo "[+] Detecting environment..."
echo "   User:      $CURRENT_USER"
echo "   Directory: $CURRENT_DIR"
echo "   Python:    $PYTHON_EXEC"

# 3. Create Service File
echo "[+] Generating systemd service file..."

cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=FOSS SOC Parsing Engine
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
ExecStart=$PYTHON_EXEC $CURRENT_DIR/main.py
Restart=always
RestartSec=5

# Logging
StandardOutput=journal
StandardError=journal

# Environment variables (Optional)
# Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 4. Enable and Start
echo "[+] Registering service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

# 5. Check Status
echo "[+] Service status:"
if systemctl is-active --quiet $SERVICE_NAME; then
    echo " Service is RUNNING!"
    echo "   To view logs: journalctl -u $SERVICE_NAME -f"
    echo "   To stop:      sudo systemctl stop $SERVICE_NAME"
else
    echo " Service failed to start. Check logs below:"
    systemctl status $SERVICE_NAME --no-pager
fi
