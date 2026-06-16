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
echo "📦 Installing required storage dependencies (open-iscsi, nfs-common)..."
sudo apt-get update -qq
sudo apt-get install -y open-iscsi nfs-common

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
