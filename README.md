# TLSOC K3s — Next-Gen Security Operations Center

Welcome to the TLSOC K3s deployment repository. This project represents a massive architectural evolution of a Security Operations Center (SOC) stack, migrating from fragile Docker Compose scripts to a highly resilient, auto-scaling, and self-healing Kubernetes architecture using **Helm**, **Longhorn**, and the **Elastic Cloud on Kubernetes (ECK) Operator**.

---

## 🏗️ How Our Stack Works

The TLSOC architecture is designed to ingest massive volumes of security logs (from web servers, firewalls, and mail servers), parse them at high speed using a custom Python engine, and visualize threats in Kibana. 

The flow of data looks like this:
1. **Target Machines (Filebeat)** send raw logs to the central **Kafka** Message Broker.
2. The **FOSS-Engine** (our custom Python parsing engine) reads the raw logs from Kafka topics (`webserver`, `mailserver`, etc.), normalizes them against our proprietary SIEM rules, and drops structured `.json` files onto a persistent hard drive (`/parser_output`).
3. **Logstash** monitors that hard drive, instantly picks up the new `.json` files, and securely pipes them into **Elasticsearch**.
4. Security Analysts log into **Kibana** (via Traefik Ingress) to view real-time dashboards and trigger alerts.

### Zero-Trust Architecture
Logstash operates under a strict Zero-Trust model. The `/parser_output` volume containing the critical JSON logs is mounted strictly as `readOnly: true` to Logstash. Logstash's internal file-tracking database (`.sincedb`) is persisted to its own isolated `logstash-state` volume, ensuring that if Logstash is ever compromised, attackers cannot modify or delete the original parsed logs.

---

## 🛠️ Technology Stack (And Why We Chose It)

| Technology | Purpose | Why We Use It |
|---|---|---|
| **K3s** | Container Orchestration | A lightweight, highly efficient Kubernetes distribution designed for edge computing and single-node development without the massive overhead of standard K8s. |
| **Helm** | Package Management | Replaces static YAML files. Allows us to dynamically inject passwords, scale replicas, and configure the entire cluster from a single `values.yaml` file. |
| **Longhorn** | Distributed Storage | Provides highly available, block-level storage across all worker nodes. Pools storage across laptops and allows databases to seamlessly migrate or survive if a node crashes. |
| **ECK Operator** | Database Management | The official Elastic Kubernetes Operator automatically handles Elasticsearch cluster formation, auto-healing dead nodes, generating secure passwords, and provisioning internal TLS certificates seamlessly. |
| **Kafka & Redis** | Data Brokering | Kafka acts as an ultra-fast shock absorber. If 50,000 logs arrive instantly during a DDoS attack, Kafka holds them in a queue so the FOSS-Engine isn't overwhelmed. Redis handles high-speed caching for IP Geolocation. |
| **Traefik** | Ingress Routing | Replaces `port-forwarding`. Traefik intercepts incoming web traffic, cleanly terminates HTTPS SSL certificates, and routes you directly to Kibana at `kibana.tlsoc.local`. |
| **HPA** | Auto-Scaling | The Horizontal Pod Autoscaler monitors CPU usage of FOSS-Engine and Logstash. When CPU exceeds 70%, it automatically spins up additional pods (up to 3) to absorb the load, then scales back down after 5 minutes of calm. |

---

## 💻 Hardware Requirements

Because Elasticsearch and Java applications require significant memory, we have tuned the default `values.yaml` limits to run on a standard developer laptop without crashing.

### Minimum Specs (Development & Testing)
* **OS:** Linux (Ubuntu 22.04+ recommended) or WSL2
* **RAM:** 8 GB (Elasticsearch requires a minimum 2GB heap, plus OS overhead)
* **CPU:** 4 Cores
* **Storage:** 20 GB free space

### Recommended Specs (Production & High Availability)
* **RAM:** 16 GB+
* **CPU:** 8 Cores
* **Storage:** 50 GB+ SSD
* **Network:** Static IP for external Log Producers to reach Kafka.

---

## 🚀 Installation Guide

> **⚠️ PRECAUTIONS (Read Before Starting):**
> - Ensure your target machine has a **stable internet connection** during installation.
> - If deploying a multi-node cluster, ensure all machines are connected to the **same physical network or VPN**, and can `ping` each other successfully.
> - Verify that K3s is fully started **before** deploying the Helm chart: `sudo systemctl status k3s`
> - Verify Kubernetes is responsive: `kubectl get nodes` should show your node as `Ready`.
> - If using WSL2, ensure Docker Desktop is **not** running — it conflicts with K3s networking.

### Step 1 — Prerequisites
Ensure Docker and Helm are installed on your Linux machine.
```bash
# Install Docker
sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker $USER && newgrp docker

# Install Helm
curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### Step 2 — Install K3s (The Control Plane)
Install the K3s engine. This turns your laptop into a Kubernetes cluster.
```bash
curl -sfL https://get.k3s.io | sh -s - \
  --write-kubeconfig-mode 644 \
  --disable servicelb \
  --tls-san $(hostname -I | awk '{print $1}')

# Setup Kubeconfig
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(whoami):$(whoami) ~/.kube/config
export KUBECONFIG=~/.kube/config
```

> **Verify K3s is running before proceeding:**
> ```bash
> sudo systemctl status k3s
> kubectl get nodes
> ```
> Your node must show `STATUS: Ready` before continuing.

### Step 3 — Install Longhorn (Distributed Storage)
Longhorn provides resilient, replicated block storage across all nodes. Install it before deploying the SOC stack.
```bash
# 1. Install storage dependencies required by Longhorn
sudo apt-get install -y open-iscsi nfs-common

# 2. Install Longhorn via Helm
helm repo add longhorn https://charts.longhorn.io
helm repo update
helm install longhorn longhorn/longhorn \
  --namespace longhorn-system \
  --create-namespace \
  --set defaultSettings.defaultReplicaCount=1

# 3. Wait for all Longhorn pods to become Ready (~2-3 minutes)
kubectl -n longhorn-system get pods -w
```
> **Note:** We set `defaultReplicaCount=1` for single-node setups. For multi-node clusters with redundancy, change this to `2` or `3`.

### Step 4 — Install the Elastic ECK Operator
Before deploying the SOC, you must install the Elastic Operator. This "robot administrator" will automatically build and secure your databases.
```bash
helm repo add elastic https://helm.elastic.co
helm repo update
helm upgrade --install elastic-operator elastic/eck-operator -n elastic-system --create-namespace
```

### Step 5 — Build the FOSS-SOC Engine Docker Image
The FOSS-SOC Engine is **not** available on Docker Hub. You must build it locally from the source code in the `engine/` directory. This image must be built on **every machine** that will run FOSS-Engine pods (the Master node and all Worker nodes).

```bash
# Clone the repository
git clone https://github.com/GodmanATG/TLSOC-K3s
cd TLSOC-K3s

# Build the Docker image locally
# Without GeoIP (basic setup):
sudo docker build -t foss-soc-engine:latest ./engine

# With GeoIP enrichment (recommended for production):
# You need a free MaxMind license key from https://www.maxmind.com/en/geolite2/signup
sudo docker build -t foss-soc-engine:latest \
  --build-arg MAXMIND_LICENSE_KEY="YOUR_KEY_HERE" ./engine
```

> **⚠️ IMPORTANT:** Because K3s uses `containerd` (not Docker) as its container runtime, the image you built with Docker is invisible to K3s. You must import it into K3s's image store:
> ```bash
> sudo docker save foss-soc-engine:latest | sudo k3s ctr images import -
> ```
> Run this command every time you rebuild the FOSS-Engine image!

### Step 6 — Configure & Deploy TLSOC via Helm
Before deploying, you **must** set your machine's IP address in the Kafka configuration.
```bash
# Get your machine's IP
hostname -I | awk '{print $1}'

# Edit the configmap in helm/tlsoc/templates/configmaps.yaml
# Replace <YOUR_MACHINE_IP> with your actual IP address
```

Deploy the entire stack:
```bash
helm install tlsoc ./helm/tlsoc
```

*Note: The `tlsoc-setup` Kubernetes Job will automatically wait for Elasticsearch and Kibana to become healthy, and then it will execute API calls to dynamically inject all Dashboards, SIEM Rules, and Data Views into Kibana.*

---

## 🖥️ Scaling to Multiple Nodes (Multi-Laptop Cluster)

TLSOC is designed to start on a single laptop and seamlessly expand to a multi-node cluster. Stateless workloads (FOSS-Engine, Logstash) will automatically distribute across all connected laptops via the HPA.

### Architecture Overview
* **Master Node (Control Plane):** Runs K3s Server, Elasticsearch, Kafka, and Redis. These stateful services are pinned here via `nodeSelector` so their data stays on one disk.
* **Worker Nodes:** Run the lightweight, stateless workloads. Kubernetes automatically schedules FOSS-Engine and Logstash replicas onto these machines to share the parsing load.

### How `add-worker.sh` Works
To make joining nodes completely painless, we wrote `add-worker.sh`. It is an interactive script that runs on the new worker laptop. Under the hood, it:
1. **Prompts** for the Master Node's IP address and cluster join token.
2. **Auto-detects** the safest network IP (or VPN IP) to route traffic to the Master node by running `ip route get <MASTER_IP>`.
3. **Installs** critical storage dependencies required by Longhorn (`open-iscsi`, `nfs-common`).
4. **Downloads** the K3s Agent binary and registers the node securely to the Master using the `--node-ip` flag to prevent VPN routing conflicts.

### Step 1 — Get the Join Credentials (on Master Node)
```bash
# Get the join token
sudo cat /var/lib/rancher/k3s/server/node-token

# Get the control plane IP
hostname -I | awk '{print $1}'
```

### Step 2 — Join the Worker (on New Laptop)
Copy the `add-worker.sh` script to the new laptop and run:
```bash
bash add-worker.sh
```
The script will interactively ask for the Master IP and Token.
Once joined, verify on the Master Node:
```bash
kubectl get nodes -o wide
```
All nodes should show `STATUS: Ready`.

---

### 📦 Worker Node Software Stack Installation

After a worker node joins the cluster, Kubernetes will automatically schedule FOSS-Engine and Logstash pods onto it. However, these pods require certain software to be pre-installed on the worker node.

#### Required Software (Install on Every Worker Node)

**1. Docker (for building FOSS-Engine image)**
```bash
sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker $USER && newgrp docker
```

**2. Build the FOSS-SOC Engine Image**
The FOSS-Engine image is **not** on Docker Hub. It must be built locally on every worker node:
```bash
# Clone the repository
git clone https://github.com/GodmanATG/TLSOC-K3s
cd TLSOC-K3s

# Build the image
sudo docker build -t foss-soc-engine:latest ./engine

# Import into K3s's containerd runtime
sudo docker save foss-soc-engine:latest | sudo k3s ctr images import -
```

**3. Longhorn Storage Dependencies**
The `add-worker.sh` script installs these automatically, but if you joined manually:
```bash
sudo apt-get install -y open-iscsi nfs-common
```

#### What runs automatically vs. what doesn't?

| Component | Runs on Worker? | Action Required? |
|---|---|---|
| FOSS-Engine | ✅ Yes (Deployment) | Must build Docker image on this node |
| Logstash | ✅ Yes (Deployment) | No action — image pulled from Docker Hub |
| Longhorn Agent | ✅ Yes (DaemonSet) | No action — auto-deployed by Longhorn |
| Traefik Proxy | ✅ Yes (DaemonSet) | No action — auto-deployed by K3s |
| Elasticsearch | ❌ No | Pinned to Master via `nodeSelector` |
| Kafka | ❌ No | Pinned to Master via `nodeSelector` |
| Redis | ❌ No | Pinned to Master via `nodeSelector` |
| Kibana | ❌ No | Runs wherever ECK schedules it (usually Master) |

---

### 🚨 Troubleshooting `add-worker.sh`
* **Hangs on "systemd: Starting k3s-agent":** This means the worker node cannot reach the Master Node on port `6443`.
  * *Fix:* Ensure the Ubuntu Firewall (`ufw`) on the Master Node is disabled or allowing port `6443`. Ensure no rogue VPNs on the worker are black-holing local traffic. Test with: `curl -k https://<MASTER_IP>:6443`
* **VPN Interface Issues:** If using a VPN (Tailscale, ProtonVPN, ZeroTier), you must join using the *VPN IP* of the Master, not the physical Wi-Fi IP. Ensure both nodes can `ping` each other over the VPN first.
* **MTU Packet Fragmentation (ContainerCreating Loop):** If pods on the new node get permanently stuck in `ContainerCreating`, or you see "TLS handshake timeout" in `journalctl -u k3s-agent`, the VPN's packet size is clashing with K3s's Flannel networking.
  * *Fix:* Edit the K3s service on the Master (`/etc/systemd/system/k3s.service`) and append `--flannel-iface=tailscale0` (or your specific VPN interface) to the ExecStart line, then restart K3s:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl restart k3s
    ```
* **FOSS-Engine pod stuck in `ImagePullBackOff` or `ErrImageNeverPull`:** The Docker image was not imported into K3s on this node.
  * *Fix:* Build and import the image on the worker node (see Worker Node Software Stack section above).
* **Worker Node shows `NotReady`:** The K3s agent lost connection to the Master.
  * *Fix:* Check if the agent is running: `sudo systemctl status k3s-agent`. If it crashed, restart it: `sudo systemctl restart k3s-agent`. Verify network connectivity to the Master: `ping <MASTER_IP>`.

---

## 💾 Longhorn Distributed Storage (Deep Dive)

Longhorn is the backbone of the TLSOC storage architecture. It creates a "Virtual Storage Pool" across all laptops and replicates the data in real-time, completely decoupling your databases from the physical motherboards they run on.

### How Longhorn Works Under the Hood
When a Pod requests a Persistent Volume Claim (PVC), Longhorn creates a hidden "Engine" controller. This Engine grabs raw 1s and 0s from the Linux kernel using iSCSI (Block Storage). When the Pod writes a file, the Engine instantly duplicates those 1s and 0s and shoots them across the network to the "Replica" controllers on the other laptops. To Kubernetes, it looks exactly like a physical hard drive is plugged into the motherboard, but Longhorn is secretly streaming it over the network in the background.

Longhorn does **NOT** split data across nodes. If you have a 5GB PVC, the entire 5GB block lives on one node. To get redundancy, Longhorn creates complete copies (replicas) of the entire block on other nodes. Actual data distribution (sharding) is handled by the database software itself (e.g., Elasticsearch clusters).

### Installation
See [Step 3 — Install Longhorn](#step-3--install-longhorn-distributed-storage) in the Installation Guide above.

### When to Use Longhorn
* **Multi-Node Laptop Clusters:** If you have 2+ nodes, Longhorn is mandatory. It ensures that if one laptop loses power, your databases survive. It provides high-speed Block-Level storage (iSCSI) required by databases.
* **Alongside Enterprise NFS:** Even if your datacenter already has a massive Enterprise NAS, Longhorn is still highly recommended. Databases (Elasticsearch, Kafka, Redis) corrupt easily on "File-Level" NFS storage. Longhorn provides the "Block-Level" translation databases demand, plus cloud-native snapshotting, cloning, and backup features that bare NFS lacks.

### When is Longhorn Redundant?
* **The Massive Singular Server:** If you deploy TLSOC in production on a massive, single bare-metal server (e.g., 64-cores, 1TB RAM), Longhorn adds unnecessary CPU overhead. There is no second machine to replicate to, so the replica feature is wasted.

#### How to Disable Longhorn (Single-Server Mode)

> **⚠️ PRECAUTION:** You must delete all existing Longhorn PVCs before switching to `local-path`. Switching the StorageClass of an existing PVC is impossible. This means all data in Longhorn volumes will be destroyed. Back up any critical data first.

1. **Uninstall Longhorn:**
   ```bash
   # First, delete all PVCs that use Longhorn
   kubectl delete pvc parser-output logstash-state foss-engine-logs -n tlsoc
   kubectl delete pvc kafka-data-kafka-0 redis-data-redis-0 -n tlsoc
   kubectl delete pvc elasticsearch-data-tlsoc-es-default-0 -n tlsoc
   
   # Uninstall Longhorn
   helm uninstall longhorn -n longhorn-system
   kubectl delete namespace longhorn-system
   ```
2. **Update Helm Templates:** Change StorageClass in **all** of these template files from `longhorn` to `local-path`:

   | File | What to Change |
   |---|---|
   | `helm/tlsoc/templates/shared-pvc.yaml` | Change `storageClassName: "longhorn"` → `"local-path"` **AND** change `accessModes` from `ReadWriteMany` → `ReadWriteOnce` (3 PVCs in this file) |
   | `helm/tlsoc/templates/kafka.yaml` | Change `storageClassName: "longhorn"` → `"local-path"` |
   | `helm/tlsoc/templates/redis.yaml` | Change `storageClassName: "longhorn"` → `"local-path"` |
   | `helm/tlsoc/templates/elastic.yaml` | Change `storageClassName: longhorn` → `local-path` |
3. **Redeploy:**
   ```bash
   helm upgrade tlsoc ./helm/tlsoc
   ```

### Accessing the Longhorn Web UI
You can graphically manage all hard drives, replicas, snapshots, and backups across your cluster:
```bash
kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80
```
Open your browser to `http://localhost:8080`.

The UI has several key pages:
* **Dashboard:** Overview of cluster storage health, volume states, and node status.
* **Volumes:** Lists all PVCs. Click a volume to see its replicas, take snapshots, or resize it.
* **Nodes:** Shows each node's storage capacity, used space, reserved space, and replica count. This is where you configure per-node storage limits.
* **Settings:** Global configuration for replica counts, data locality, automatic salvage, etc.

### Configuring Storage Limits Per Node
To restrict how much storage Longhorn can use on a specific laptop:
1. Open the Longhorn UI → **Node** tab.
2. Click **Edit Node** on the target laptop.
3. Set **Storage Reserved** to `(Total Disk Size - Desired Longhorn Limit)`. For example, if the disk is 60GB and you want Longhorn to use at most 10GB, set Storage Reserved to `50Gi`.

### Operational Guide (Replicas, Snapshots, Backups)
* **Replicas (Live Clones):** By default, Longhorn creates physical copies of your data. To live-migrate data to another laptop:
  1. Open the UI, click a Volume, and click **Update Replicas Count** (e.g., increase by 1).
  2. Longhorn will automatically stream a clone over the network to a new node.
  3. Once the new replica turns **Healthy** (solid blue), decrease the replica count and delete the old replica to finalize the migration.
* **Snapshots (The Undo Button):** Snapshots instantly freeze the hard drive in time. Take a snapshot before upgrading databases. If the upgrade corrupts the database, click "Revert" in the UI to instantly travel back in time.
  * To take a snapshot: Click a Volume → Click **Take Snapshot**.
  * To revert: Click the snapshot in the timeline → Click **Revert**.
* **Backups (Disaster Recovery):** Snapshots are kept on the local disk. Backups upload that snapshot off-site (to AWS S3 or an NFS server). Configure your Backup Target in `Settings > General > Backup Target`.

### Key Longhorn Settings
| Setting | Recommended Value | What It Does |
|---|---|---|
| Default Replica Count | `1` (single-node) / `2` (multi-node) | Number of physical copies of each volume. |
| Default Data Locality | `best-effort` | Tries to keep data on the same node as the pod using it, maximizing read/write speed. |
| Automatic Salvage | `Enabled` | If all replicas go faulty (e.g., all nodes lost power simultaneously), Longhorn will try to recover the most intact replica automatically. |
| Auto Delete Workload Pod on Detach | `Enabled` | If a node crashes and the volume detaches, Kubernetes will auto-kill and respawn the pod on a healthy node where a backup replica exists. |
| Storage Over Provisioning % | `100` | Prevents Longhorn from promising more storage than physically exists. |

### Troubleshooting Longhorn
* **Volumes Stuck in "Degraded":** A node disconnected. Longhorn is warning you that it doesn't have enough physical copies. Reconnect the node, or delete the dead replica in the UI so Longhorn can rebuild it on a healthy node.
* **Volumes Stuck in "Detached":** Ensure the Pod is actively scheduled. If a node crashes, enable the setting `Automatically Delete Workload Pod when Volume Detaches` so Kubernetes forces the Pod to respawn on a healthy node where the backup replica lives.
* **"Scheduling Failure" on a Volume:** The remaining nodes don't have enough free storage. Either free up space or add another node.
* **Red warnings for `KernelModulesLoaded` or `RequiredPackages`:** Install the missing dependencies: `sudo apt-get install -y open-iscsi nfs-common`. If the node still shows the warning but the overall status is `Ready`, Longhorn is falling back to basic block storage and will still work.

---

## 🌐 Accessing the SOC (Kibana)

You do not need to port-forward! Traefik handles the routing locally.
Open your browser and navigate to: **`https://localhost`** or **`https://kibana.tlsoc.local`**
*(Note: Since the SSL certificate is self-signed, you will need to bypass the browser security warning).*

**How to get your auto-generated Password:**
The ECK Operator generates a highly secure random password on every fresh install. Fetch it via:
```bash
kubectl get secret tlsoc-es-elastic-user -n tlsoc -o jsonpath='{.data.elastic}' | base64 -d
```
* **Username:** `elastic`
* **Password:** *(Output from the command above)*

---

## 📖 Operational Cheat Sheet

### Cluster Health & Status
```bash
# Check all nodes and their status
kubectl get nodes -o wide

# See ALL pods across ALL namespaces
kubectl get pods -A -o wide

# Check if K3s is running on Master
sudo systemctl status k3s

# Check if K3s agent is running on Worker
sudo systemctl status k3s-agent

# See Persistent Storage Volumes (Hard drives)
kubectl get pvc -n tlsoc

# See which StorageClass each PVC is using
kubectl get pvc -n tlsoc -o custom-columns=NAME:.metadata.name,SIZE:.spec.resources.requests.storage,STORAGECLASS:.spec.storageClassName

# Check Longhorn volume health
kubectl get volumes.longhorn.io -n longhorn-system
```

### Monitoring & Resource Usage
```bash
# See CPU and RAM usage of all pods
kubectl top pods -n tlsoc

# See CPU and RAM usage of all nodes
kubectl top nodes

# Check HPA scaling status (current vs target CPU)
kubectl get hpa -n tlsoc

# Describe HPA for detailed scaling events
kubectl describe hpa foss-engine-hpa -n tlsoc
```

### Viewing Logs
```bash
# Watch the FOSS-Engine parse logs in real-time
kubectl logs -l app=foss-engine -n tlsoc -f --tail=50

# Watch Logstash pipeline logs
kubectl logs -l app=logstash -n tlsoc -f --tail=50

# Watch Kafka broker logs
kubectl logs -l app=kafka -n tlsoc -f --tail=50

# Watch Elasticsearch logs
kubectl logs -l common.k8s.elastic.co/type=elasticsearch -n tlsoc -f --tail=50

# Watch Kibana logs
kubectl logs -l common.k8s.elastic.co/type=kibana -n tlsoc -f --tail=50

# Check Longhorn manager logs (storage issues)
kubectl logs -l app=longhorn-manager -n longhorn-system -f --tail=50
```

### Upgrading Configurations
If you edit `helm/tlsoc/values.yaml` (e.g., to increase replicas or RAM), apply the changes live without deleting data:
```bash
helm upgrade tlsoc ./helm/tlsoc
```

### Scaling
```bash
# Manually scale FOSS-Engine to 3 replicas
kubectl scale deployment foss-engine -n tlsoc --replicas=3

# Manually scale Logstash to 2 replicas
kubectl scale deployment logstash -n tlsoc --replicas=2

# Or edit values.yaml and run helm upgrade (persistent change)
```

### Starting & Stopping the Cluster
```bash
# Stop Worker node first (run on Worker)
sudo systemctl stop k3s-agent
sudo /usr/local/bin/k3s-killall.sh   # cleanly unmounts all volumes

# Stop Master node last (run on Master)
sudo systemctl stop k3s
sudo /usr/local/bin/k3s-killall.sh

# Start Master node first (run on Master)
sudo systemctl start k3s

# Start Worker node after Master is up (run on Worker)
sudo systemctl start k3s-agent
```

### Removing a Worker Node
```bash
# On Master: drain the node (gracefully moves all pods off)
kubectl drain <NODE_NAME> --ignore-daemonsets --delete-emptydir-data

# On Master: delete the node from the cluster
kubectl delete node <NODE_NAME>

# On Worker: uninstall K3s agent
sudo /usr/local/bin/k3s-agent-uninstall.sh
```

### Hard Reset (Wipe Everything)
If you want to completely erase the database and start from zero:
```bash
helm uninstall tlsoc
kubectl delete pvc --all -n tlsoc
# Wait 30 seconds, then reinstall:
helm install tlsoc ./helm/tlsoc
```

### Longhorn Storage Management
```bash
# Open the Longhorn UI
kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80

# Check physical Longhorn storage usage on a node (run on that node)
sudo du -sh /var/lib/longhorn/

# List individual replica files on a node
sudo ls -lh /var/lib/longhorn/replicas/
```

---

## 🐛 Troubleshooting & Common Bugs

### 1. `kubectl: command not found`
**Why:** K3s bundles `kubectl` inside the `k3s` command.
**The Fix:** Create a symlink so `kubectl` works natively:
```bash
sudo ln -s /usr/local/bin/k3s /usr/local/bin/kubectl
```

### 2. Sudo Permission Denied (`localhost:8080 was refused`)
**Why:** K3s locks the cluster configuration file to the `root` user by default.
**The Fix:** Copy the config to your home directory and take ownership of it:
```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(whoami):$(whoami) ~/.kube/config
export KUBECONFIG=~/.kube/config
```

### 3. WSL2 Crash: `Failed to start ContainerManager...`
**Why:** Windows Subsystem for Linux (WSL2) handles `cgroups` differently from a native Linux kernel.
**The Fix:** Tell K3s to use the `systemd` cgroup driver:
```bash
sudo k3s server --kubelet-arg="--cgroup-driver=systemd"
```

### 4. Pods stuck in `Terminating` forever
**Why:** A node hard-crashed or the storage volume got forcefully detached.
**The Fix:** Force delete the pod:
```bash
kubectl delete pod <pod-name> -n tlsoc --grace-period=0 --force
```

### 5. FOSS-Engine pod in `ErrImageNeverPull` or `ImagePullBackOff`
**Why:** The `foss-soc-engine:latest` Docker image is not on Docker Hub. It must be built locally on every node that runs the engine. The image was either not built or not imported into K3s.
**The Fix:**
```bash
sudo docker build -t foss-soc-engine:latest ./engine
sudo docker save foss-soc-engine:latest | sudo k3s ctr images import -
```

### 6. FOSS-Engine not parsing logs / "No Kafka brokers available"
**Why:** The bootstrap server in the engine's ConfigMap doesn't match your Kafka deployment, or Kafka hasn't finished starting.
**The Fix:**
1. Verify Kafka is running: `kubectl get pods -l app=kafka -n tlsoc`
2. Check the engine ConfigMap: `kubectl get configmap engine-config -n tlsoc -o yaml`. The `bootstrap_servers` should be `kafka.tlsoc.svc.cluster.local:9092` for in-cluster communication.
3. Restart the engine: `kubectl rollout restart deployment foss-engine -n tlsoc`

### 7. Logstash not shipping logs to Elasticsearch
**Why:** Logstash can't reach Elasticsearch, the TLS certificate is wrong, or the Elastic password changed.
**The Fix:**
1. Check Logstash logs for errors: `kubectl logs -l app=logstash -n tlsoc --tail=100`
2. Verify the ES secret exists: `kubectl get secret tlsoc-es-elastic-user -n tlsoc`
3. Restart Logstash: `kubectl rollout restart deployment logstash -n tlsoc`

### 8. Elasticsearch pod stuck in `CrashLoopBackOff`
**Why:** Elasticsearch ran out of memory (OOM killed), or the disk is full, or the `vm.max_map_count` kernel parameter is too low.
**The Fix:**
```bash
# Check if it was OOM killed
kubectl describe pod -l common.k8s.elastic.co/type=elasticsearch -n tlsoc | grep -A5 "Last State"

# Increase memory limits in values.yaml and upgrade
# Or set the kernel parameter (required for ES):
sudo sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```

### 9. Kafka pod in `CrashLoopBackOff`
**Why:** Usually caused by corrupted KRaft metadata or a Kafka version mismatch after upgrade.
**The Fix:**
```bash
# Check Kafka logs
kubectl logs kafka-0 -n tlsoc --tail=100

# Nuclear option: delete Kafka data and restart
kubectl delete pvc kafka-data-kafka-0 -n tlsoc
kubectl delete pod kafka-0 -n tlsoc
```

### 10. HPA not scaling (always shows `<unknown>` for CPU)
**Why:** The K3s Metrics Server is not deployed or not responding.
**The Fix:**
```bash
# Check if metrics-server is running
kubectl get pods -n kube-system | grep metrics

# If not present, K3s usually includes it. Restart K3s:
sudo systemctl restart k3s

# Verify metrics are flowing
kubectl top pods -n tlsoc
```

### 11. Longhorn Volumes stuck in "Degraded"
**Why:** A node was removed or went offline, and Longhorn lost a replica.
**The Fix:** Open the Longhorn UI, click the volume, and delete the dead replica. If `Default Replica Count` is higher than the number of healthy nodes, reduce it.

### 12. PVC stuck in `Pending` state
**Why:** No StorageClass can satisfy the claim, or Longhorn is not installed.
**The Fix:**
```bash
# Check available StorageClasses
kubectl get storageclass

# If "longhorn" is missing, install Longhorn (see Step 3)
# If using local-path, ensure the PVC uses storageClassName: "local-path"
kubectl describe pvc <pvc-name> -n tlsoc
```

---

## 👁️ Visualizing the Cluster with OpenLens

Instead of relying entirely on terminal commands, you can use **OpenLens** (a powerful, open-source desktop GUI for Kubernetes) to visually manage your SOC cluster.

### What it does:
OpenLens allows you to graphically see all your pods, view live scrolling logs, access terminal shells inside containers, and monitor CPU/RAM usage across your FOSS-Engine and Logstash deployments in real-time.

### How to Connect OpenLens to your WSL2 K3s Cluster:
If you are running K3s inside WSL2 on Windows, OpenLens runs on your host Windows machine. You need to pull the configuration out of WSL and point OpenLens to it.

1. Open a **Windows PowerShell** terminal (not Ubuntu).
2. Run this command to copy the K3s config to your Windows profile and point it to localhost:
```powershell
wsl -d <YOUR_WSL_DISTRO> -e bash -c "sudo cp /etc/rancher/k3s/k3s.yaml /mnt/c/Users/<YOUR_WINDOWS_USERNAME>/.kube/config; sudo chmod 644 /mnt/c/Users/<YOUR_WINDOWS_USERNAME>/.kube/config; sed -i 's/127.0.0.1/localhost/g' /mnt/c/Users/<YOUR_WINDOWS_USERNAME>/.kube/config"
```
3. Open the **OpenLens** application on Windows. It will automatically connect!

---

## 🐍 The FOSS-SOC Engine (Deep Dive)

The **FOSS-Engine** is the custom-built, ultra-fast core of the TLSOC architecture. While standard SOCs use bulky, slow log parsers (like native Logstash Grok filters or Fluentd) to do heavy string manipulation, TLSOC pushes all of the complex log normalization, regex parsing, and threat intelligence enrichment into this specialized, lightweight Python application.

### Why Build a Custom Engine?
Security logs generated by firewalls, web servers, and mail servers come in completely different formats. An Apache web server log looks nothing like a ModSecurity WAF log, which looks nothing like a Postfix mail log. If you feed raw, chaotic text into Elasticsearch, your Kibana Dashboards will break because they won't know where to find the "IP Address" or the "Username."

The FOSS-Engine solves this problem. It acts as a massive universal translator.

### The 5-Stage Pipeline

#### Stage 1: Ingestion (Kafka Consumer)
The Engine operates as a high-throughput **Kafka Consumer Group** (`group_id: foss-soc-engine`). It subscribes to a regex topic pattern `^(webserver|vulnerability|mailserver|proxyserver|summersoc|.*)$` to listen to all log streams simultaneously. Because it uses a Consumer Group, multiple engine pods can run concurrently, perfectly splitting the log parsing workload without duplicating logs. Kafka automatically load-balances partitions across all engine replicas.

#### Stage 2: Rule Mapping (`program_mapping`)
When a log arrives, it has a metadata tag (e.g., `waf-nginx-access`). The Engine reads the `program_mapping` section of its `config.yaml` to determine which rule file to use:
```yaml
program_mapping:
  waf-nginx-access: "apache_access"     # Nginx access → Apache rule (same format)
  waf_auth: "linux_auth"               # SSH/Sudo logs → Linux auth rule
  modsec_audit_log: "modsec"           # WAF alerts → ModSecurity rule
  postfix: "postfix"                   # Mail relay → Postfix rule
  roundcube_login: "roundcube"         # Webmail login → Roundcube rule
```

#### Stage 3: Parsing (Regex & Normalization)
The Engine opens the corresponding `.yaml` rule file in the `rules/` directory. These files contain highly optimized Regular Expressions. For example, the `linux_auth.yaml` rule can extract the word `Failed password` from a Linux auth log and categorize it under `event.outcome: failure` and `event.category: authentication`. Every field is mapped to the **Elastic Common Schema (ECS)** so that Kibana dashboards work universally regardless of which server generated the log.

#### Stage 4: Enrichment (GeoIP & Redis Cache)
If the log contains a public IP address (`source.ip`), the Engine performs an ultra-fast local lookup against a **MaxMind GeoLite2** database (`database/GeoLite2-City.mmdb`). It attaches Country, City, and Latitude/Longitude coordinates to the log. For extreme speed, it uses **Redis** to cache IPs it has already looked up, so repeated requests for the same IP are answered in microseconds instead of milliseconds.

#### Stage 5: Output (Batched File Drop)
Once the log is a perfectly formatted JSON object, the Engine batches up to **1000 logs** (or waits **5 seconds**, whichever comes first) and drops them as a single `.json` file into the `/var/log/soc_output/` directory. This directory is a shared Longhorn RWX volume (`parser-output` PVC). Logstash monitors this directory, instantly picks up the new files, and securely pipes the structured JSON into Elasticsearch.

### Source Code & Image Build

The entire engine source code lives in the `engine/` directory:
```
engine/
├── Dockerfile           # Multi-stage Docker build (Python 3.11-slim)
├── main.py              # Entry point — Kafka consumer loop, batching, signal handling
├── config.yaml          # Local dev config (Kubernetes uses ConfigMap override)
├── requirements.txt     # Python deps: kafka-python-ng, redis, geoip2, PyYAML, python-snappy
├── core/
│   ├── engine.py        # Core parsing logic — applies rules to raw logs
│   ├── schema.py        # LogInput dataclass — ECS field definitions
│   └── registry.py      # RuleRegistry — loads and caches YAML rule files
├── utils/
│   └── geoip.py         # GeoIP lookup and Redis caching logic
└── rules/
    ├── apache_access.yaml   # Apache/Nginx access log parser
    ├── linux_auth.yaml      # SSH, Sudo, Su authentication parser
    ├── modsec.yaml          # ModSecurity WAF alert parser
    ├── postfix.yaml         # Postfix mail relay parser
    └── roundcube.yaml       # Roundcube webmail login parser
```

### Building the Image
See [Step 5 — Build the FOSS-SOC Engine Docker Image](#step-5--build-the-foss-soc-engine-docker-image) in the Installation Guide above.

### Key Configuration (ConfigMap vs config.yaml)
The `engine/config.yaml` file is for **local development only**. When running inside Kubernetes, the engine's config is overridden by the `engine-config` ConfigMap defined in `helm/tlsoc/templates/configmaps.yaml`. This ConfigMap sets:
* `bootstrap_servers` to the internal Kubernetes DNS name (`kafka.tlsoc.svc.cluster.local:9092`) instead of an external IP.
* `group_id` to `foss-soc-engine` (the Kubernetes consumer group).
* `redis.host` to `redis.tlsoc.svc.cluster.local`.

If you need to change the Kafka bootstrap server, edit the ConfigMap in `configmaps.yaml`, not the `engine/config.yaml` file. Then run `helm upgrade`.

### Security Model
The Dockerfile enforces a non-root security model. The engine runs as `appuser` (UID 1001), not root. This ensures that even if an attacker exploits a vulnerability in the Python code, they cannot escape the container or modify system files.
