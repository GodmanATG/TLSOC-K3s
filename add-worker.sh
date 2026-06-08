#!/bin/bash
# ================================================================
# add-worker.sh — Join a worker node to the TLSOC k3s cluster
# ================================================================
# Run this script on Laptop 2 or Laptop 3 to join them as workers.
# The control plane must already be running on Laptop 1.
#
# Usage:
#   bash add-worker.sh <LAPTOP1_IP> <TOKEN>
#
# Get token from Laptop 1:
#   sudo cat /var/lib/rancher/k3s/server/node-token
#
# Get IP from Laptop 1:
#   hostname -I | awk '{print $1}'
# ================================================================

set -e

if [ -z "$1" ] || [ -z "$2" ]; then
  echo "Usage: bash add-worker.sh <SERVER_IP> <TOKEN>"
  echo ""
  echo "Get these from Laptop 1 (the control plane):"
  echo "  SERVER_IP:  hostname -I | awk '{print \$1}'"
  echo "  TOKEN:      sudo cat /var/lib/rancher/k3s/server/node-token"
  exit 1
fi

SERVER_IP=$1
TOKEN=$2

echo "═══════════════════════════════════════════════════"
echo "  TLSOC — Joining Worker Node to Cluster"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Control Plane: https://${SERVER_IP}:6443"
echo "  This machine:  $(hostname)"
echo ""

# Install k3s as an agent (worker node)
curl -sfL https://get.k3s.io | K3S_URL="https://${SERVER_IP}:6443" K3S_TOKEN="${TOKEN}" sh -

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ Worker node joined successfully!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  On Laptop 1, run:"
echo "    kubectl get nodes"
echo "  You should see this machine listed as Ready."
echo ""
echo "  Kubernetes will automatically schedule"
echo "  Engine and Logstash replicas onto this node"
echo "  when scaling is triggered."
echo "═══════════════════════════════════════════════════"
