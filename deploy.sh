#!/bin/bash
# ================================================================
# deploy.sh — One-command deployment of the entire TLSOC K3s stack
# ================================================================
# This script handles everything:
#   1. Generates secrets from TLS certs
#   2. Builds the FOSS Engine Docker image and loads it into k3s
#   3. Creates Kubernetes resources in the correct order
#   4. Creates the setup-files ConfigMap from ndjson files
#   5. Waits for pods to be ready
#   6. Runs the Setup Job
#   7. Sets up port-forwarding for Kibana access
#
# Usage:   cd TLSOC-K3s && bash deploy.sh
# ================================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ${BOLD}TLSOC K3s — Full Stack Deployment${NC}"
echo "════════════════════════════════════════════════════════════"
echo ""

# ─── Pre-flight checks ─────────────────────────────────────────
echo "${YELLOW}[1/8] Pre-flight checks...${NC}"

if ! command -v kubectl &> /dev/null; then
  echo "${RED}❌ kubectl not found. Install k3s first.${NC}"
  exit 1
fi

if ! command -v docker &> /dev/null; then
  echo "${RED}❌ docker not found. Install Docker first.${NC}"
  exit 1
fi

# Check if k3s is running
if ! kubectl get nodes &> /dev/null; then
  echo "${RED}❌ Cannot connect to Kubernetes cluster.${NC}"
  echo "   Start k3s with: sudo k3s server --write-kubeconfig-mode 644"
  exit 1
fi

echo "${GREEN}✅ kubectl connected. Cluster nodes:${NC}"
kubectl get nodes
echo ""

# ─── Step 1: Generate secrets ──────────────────────────────────
echo "${YELLOW}[2/8] Generating secrets from TLS certificates...${NC}"
bash generate-secrets.sh
echo ""

# ─── Step 2: Build and import Engine image ─────────────────────
echo "${YELLOW}[3/8] Building FOSS SOC Engine Docker image...${NC}"
docker build -t foss-soc-engine:latest ./engine/
echo ""

echo "${YELLOW}     Loading image into k3s...${NC}"
# Check if running in k3s or Docker Desktop
if command -v k3s &> /dev/null; then
  docker save foss-soc-engine:latest | sudo k3s ctr images import -
  echo "${GREEN}✅ Image loaded into k3s${NC}"
else
  echo "${GREEN}✅ Image available via Docker Desktop (shared daemon)${NC}"
fi
echo ""

# ─── Step 3: Apply Kubernetes resources in order ───────────────
echo "${YELLOW}[4/8] Creating Kubernetes resources...${NC}"

echo "   → Namespaces"
kubectl apply -f k8s/00-namespaces.yaml

echo "   → Secrets"
kubectl apply -f k8s/01-secrets.yaml

echo "   → ConfigMaps"
kubectl apply -f k8s/02-configmaps.yaml

echo "   → Elasticsearch (StatefulSet)"
kubectl apply -f k8s/03-elasticsearch.yaml

echo "   → Kafka (StatefulSet)"
kubectl apply -f k8s/04-kafka.yaml

echo "   → Redis (StatefulSet)"
kubectl apply -f k8s/05-redis.yaml

echo "   → Shared PVCs"
kubectl apply -f k8s/06-shared-pvc.yaml

echo "   → FOSS Engine (Deployment)"
kubectl apply -f k8s/07-foss-engine.yaml

echo "   → Logstash (Deployment)"
kubectl apply -f k8s/08-logstash.yaml

echo "   → Kibana (Deployment + Ingress)"
kubectl apply -f k8s/09-kibana.yaml

echo "   → RBAC"
kubectl apply -f k8s/13-rbac.yaml

echo "   → HPA (Autoscaling)"
kubectl apply -f k8s/10-hpa.yaml
echo ""

# ─── Step 4: Create setup-files ConfigMap ──────────────────────
echo "${YELLOW}[5/8] Creating setup-files ConfigMap (dashboards + SIEM rules)...${NC}"
kubectl create configmap setup-files \
  --from-file=dashboards.ndjson=./setup/dashboards.ndjson \
  --from-file=rules.ndjson=./setup/rules.ndjson \
  --from-file=logo.png=./setup/logo.png \
  -n tlsoc-ops \
  --dry-run=client -o yaml | kubectl apply -f -
echo "${GREEN}✅ Setup files loaded${NC}"
echo ""

# ─── Step 5: Wait for core services ───────────────────────────
echo "${YELLOW}[6/8] Waiting for pods to be ready (this takes 3-5 minutes)...${NC}"
echo "   Watching pod status — press Ctrl+C when all show Running/Completed"
echo ""

echo "   Waiting for Elasticsearch..."
kubectl rollout status statefulset/elasticsearch -n tlsoc --timeout=300s 2>/dev/null || \
  echo "   ⏳ ES still starting (this is normal, it takes ~3 min)..."

echo "   Waiting for Kafka..."
kubectl rollout status statefulset/kafka -n tlsoc --timeout=120s 2>/dev/null || true

echo "   Waiting for Redis..."
kubectl rollout status statefulset/redis -n tlsoc --timeout=60s 2>/dev/null || true

echo ""
echo "${GREEN}Current pod status:${NC}"
kubectl get pods -n tlsoc -o wide
echo ""

# ─── Step 6: Run setup job ─────────────────────────────────────
echo "${YELLOW}[7/8] Running Setup Job (configures Kibana space, dashboards, SIEM rules)...${NC}"

# Delete any previous setup job
kubectl delete job tlsoc-setup -n tlsoc-ops 2>/dev/null || true

kubectl apply -f k8s/12-setup-job.yaml
echo "   Watching setup logs (Ctrl+C when you see 'TLSOC IS READY')..."
echo ""

# Wait a bit for the job pod to spawn
sleep 10
kubectl logs job/tlsoc-setup -n tlsoc-ops -f 2>/dev/null || \
  echo "   Setup job still starting... check with: kubectl logs job/tlsoc-setup -n tlsoc-ops -f"

echo ""

# ─── Step 7: Access instructions ───────────────────────────────
echo "${YELLOW}[8/8] Access Kibana${NC}"
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ${GREEN}${BOLD}🚀 TLSOC IS DEPLOYED!${NC}"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  ${BOLD}Option A — Port Forward (works everywhere):${NC}"
echo "    kubectl port-forward svc/kibana 5601:5601 -n tlsoc"
echo "    Then open: ${BOLD}https://localhost:5601${NC}"
echo ""
echo "  ${BOLD}Option B — Ingress (k3s only):${NC}"
echo "    Add to /etc/hosts: $(hostname -I 2>/dev/null | awk '{print $1}')  kibana.tlsoc.local"
echo "    Then open: ${BOLD}https://kibana.tlsoc.local${NC}"
echo ""
echo "  ${BOLD}Login:${NC}"
echo "    Username: elastic"
echo "    Password: DTJvrIiQdd6C5D1dKO=-"
echo "    Space:    TLSOC (click the tile after login)"
echo ""
echo "  ${BOLD}Useful commands:${NC}"
echo "    kubectl get pods -n tlsoc -o wide"
echo "    kubectl logs -l app=foss-engine -n tlsoc -f"
echo "    kubectl logs job/tlsoc-setup -n tlsoc-ops -f"
echo "════════════════════════════════════════════════════════════"
