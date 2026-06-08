#!/bin/bash
# ================================================================
# teardown.sh — Clean removal of the entire TLSOC stack
# ================================================================
# Deletes all pods, services, PVCs, and namespaces.
# Use this to reset the cluster for a fresh demo or to clean up.
#
# Usage:   bash teardown.sh
# WARNING: This deletes ALL TLSOC data including Elasticsearch indices!
# ================================================================

set -e

echo "═══════════════════════════════════════════════════"
echo "  ⚠️  TLSOC — Full Stack Teardown"
echo "═══════════════════════════════════════════════════"
echo ""
read -p "This will DELETE all TLSOC data. Are you sure? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Cancelled."
  exit 0
fi

echo ""
echo "Deleting all resources..."

kubectl delete namespace tlsoc --ignore-not-found=true
kubectl delete namespace tlsoc-ops --ignore-not-found=true

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ TLSOC stack removed."
echo "  To redeploy: bash deploy.sh"
echo "═══════════════════════════════════════════════════"
