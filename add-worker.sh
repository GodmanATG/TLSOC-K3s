#!/bin/bash
# ================================================================
# add-worker.sh — Join a worker node to the TLSOC k3s cluster
# ================================================================
# Run this script on any new Ubuntu laptop to join it as a worker.
# It automatically installs storage dependencies and avoids VPN routing issues.
# ================================================================

set -e

echo "═══════════════════════════════════════════════════"
echo "  TLSOC — Interactive Worker Node Installer"
echo "═══════════════════════════════════════════════════"
echo ""

# 1. Prompt for Control Plane details
read -p "Enter the Master Node IP (e.g. 192.168.10.63): " SERVER_IP
if [ -z "$SERVER_IP" ]; then
    echo "❌ Master IP is required!"
    exit 1
fi

echo ""
echo "You can get the cluster token by running this on the Master Node:"
echo "sudo cat /var/lib/rancher/k3s/server/node-token"
echo ""
read -p "Enter the Cluster Token: " TOKEN
if [ -z "$TOKEN" ]; then
    echo "❌ Token is required!"
    exit 1
fi

# 2. Automatically detect the correct Wi-Fi IP
echo ""
echo "🔍 Detecting correct local network interface..."
# This asks the OS: "Which IP would you use to talk to the Master node?"
# This guarantees we pick the Wi-Fi card instead of the VPN!
LOCAL_IP=$(ip -4 route get $SERVER_IP | awk '{print $7}' | head -n 1)

if [ -z "$LOCAL_IP" ]; then
    echo "⚠️  Could not automatically detect the route to the Master Node."
    read -p "Please enter this laptop's Wi-Fi IP manually: " LOCAL_IP
else
    echo "✅ Found safe route to Master Node via local IP: $LOCAL_IP"
fi

# 3. Install Storage Dependencies for Longhorn
echo ""
echo "📦 Installing required storage dependencies (open-iscsi, nfs-common, cryptsetup)..."

if sudo apt-get update && sudo apt-get install -y open-iscsi nfs-common cryptsetup; then
    echo "✅ Dependencies installed successfully."
    sudo systemctl enable --now iscsid
else
    echo "⚠️  WARNING: Failed to install storage dependencies automatically!"
    echo "    Your package manager might be locked or offline."
    echo "    Longhorn requires these packages to function properly."
    echo "    Please run this command manually later:"
    echo "    sudo apt-get update && sudo apt-get install -y open-iscsi nfs-common cryptsetup"
fi

echo ""
echo "🔄 Loading iscsi_tcp kernel module required for Longhorn..."
if sudo modprobe iscsi_tcp; then
    echo "✅ iscsi_tcp module loaded."
    # Make it persistent across reboots
    if ! grep -q "^iscsi_tcp" /etc/modules; then
        echo "iscsi_tcp" | sudo tee -a /etc/modules > /dev/null
    fi
else
    echo "⚠️  WARNING: Failed to load iscsi_tcp module!"
    echo "    Longhorn volumes may fail to attach on this node."
fi

# 4. Install K3s Agent
echo ""
echo "🚀 Joining the TLSOC Cluster..."
curl -sfL https://get.k3s.io | K3S_URL="https://${SERVER_IP}:6443" K3S_TOKEN="${TOKEN}" sh -s - --node-ip ${LOCAL_IP}

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ Worker node joined successfully!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  On your Master Node, run:"
echo "    sudo k3s kubectl get nodes -o wide"
echo "  You should see this machine ($LOCAL_IP) listed as Ready."
echo "═══════════════════════════════════════════════════"
