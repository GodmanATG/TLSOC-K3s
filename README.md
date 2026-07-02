# TLSOC K3s — Next-Gen Security Operations Center

Welcome to the TLSOC K3s deployment repository. This project represents a massive architectural evolution of a Security Operations Center (SOC) stack, migrating from fragile Docker Compose scripts to a highly resilient, auto-scaling, and self-healing Kubernetes architecture using **Helm**, **Longhorn**, and the **Elastic Cloud on Kubernetes (ECK) Operator**.

---

## 🏗️ How Our Stack Works

The TLSOC architecture is engineered to ingest massive volumes of raw security logs from endpoints across your network, parse them at lightning speed using a horizontally scaling Python engine, and securely visualize the normalized threats in Kibana. We achieve this by replacing heavy endpoint agents with native OS tools, leveraging `rsyslog` and `omkafka`.

The complete data pipeline operates seamlessly:
1. **Target Machines (`rsyslog` + `omkafka`)**: Rather than installing heavy agents, target Linux machines use their native `rsyslog` daemon equipped with the `omkafka` module to forward raw system and application logs directly to the central **Kafka** Message Broker.
2. **Kafka Shock Absorber**: Kafka securely queues the incoming logs, ensuring that sudden log floods (like a DDoS attack) never overwhelm the parsing engine or drop data.
3. **The FOSS-Engine**: Our custom Python parsing engine reads raw logs from Kafka topics (e.g., `webserver`, `mailserver`). It applies high-performance regex parsing, normalizes fields to the Elastic Common Schema (ECS), enriches IP addresses with GeoIP locations, and outputs structured `.json` files onto an isolated Longhorn hard drive.
4. **Logstash Sidecar**: Logstash (running inside the exact same pod as the FOSS-Engine) continuously monitors the hard drive. It instantly detects new `.json` files via block-storage events and securely pipes the structured data directly into **Elasticsearch**.
5. **Kibana Dashboards**: Security Analysts log into **Kibana** (routed securely via Traefik Ingress) to view real-time SIEM dashboards, hunt for threats, and trigger alerts.

---

## 🛠️ Technology Stack (And Why We Chose It)

| Technology | Purpose | Why We Use It |
|---|---|---|
| **K3s** | Container Orchestration | A lightweight, highly efficient Kubernetes distribution designed for edge computing and single-node development without the massive overhead of standard K8s. |
| **Helm** | Package Management | Replaces static YAML files. Allows us to dynamically inject passwords, scale replicas, and configure the entire cluster from a single `values.yaml` file. |
| **Longhorn** | Distributed Storage | Provides highly available, block-level storage across all worker nodes. Pools storage across laptops and allows databases to seamlessly migrate or survive if a node crashes. |
| **ECK Operator** | Database Management | The official Elastic Kubernetes Operator automatically handles Elasticsearch cluster formation, auto-healing dead nodes, generating secure passwords, and provisioning internal TLS certificates seamlessly. |
| **Kafka & Redis** | Data Brokering | Kafka acts as an ultra-fast shock absorber. If 50,000 logs arrive instantly during a DDoS attack, Kafka holds them in a queue so the FOSS-Engine isn't overwhelmed. Redis acts as a short-term memory state store for multi-line log parsing. |
| **Traefik** | Ingress Routing | Replaces `port-forwarding`. Traefik intercepts incoming web traffic, cleanly terminates HTTPS SSL certificates, and routes you directly to Kibana at `kibana.tlsoc.local`. |
| **Cert-Manager** | Security (TLS) | Acts as an internal Certificate Authority that dynamically provisions and automatically rotates TLS certificates for Kafka, Kibana, and Logstash. Eliminates manual certificate management. |
| **KEDA** | Event-Driven Auto-Scaling | Scales the FOSS-Engine based on real Kafka consumer lag. When thousands of unread logs pile up on the Kafka broker, KEDA spins up more FOSS-Engine pods to burn through the backlog instantly. Far more precise than CPU-based scaling for queue-driven workloads. |
| **VPA** | Vertical Resource Tuning | Watches all 6 services in observation mode and generates mathematically perfect CPU/RAM recommendations based on actual historical usage. Use its output to tune `values.yaml` and prevent OOM-kills or resource starvation. |

---

## 💻 Hardware Requirements

Because Elasticsearch and Java applications require significant memory, we have tuned the default `values.yaml` limits to run on a standard developer laptop without crashing.

### Minimum Specs (Development & Testing)
* **OS:** Linux (Ubuntu 22.04+ recommended)
* **RAM:** 8 GB (Elasticsearch requires a minimum 2GB heap, plus OS overhead)
* **CPU:** 4 Cores
* **Storage:** 30 GB+ free space (Container images and distributed block storage require significant overhead)

### Recommended Specs (Production & High Availability)
* **RAM:** 16 GB+
* **CPU:** 8 Cores
* **Storage:** 100 GB+ SSD
* **Network:** Static IP for external Log Producers to reach Kafka.

---

## 🚀 Installation Guide

> **⚠️ PRECAUTIONS (Read Before Starting):**
> - Ensure your target machine has a **stable internet connection** during installation.
> - If deploying a multi-node cluster, ensure all machines are connected to the **same physical network or VPN**, and can `ping` each other successfully.
> - Verify that K3s is fully started **before** deploying the Helm chart: `sudo systemctl status k3s`
> - Verify Kubernetes is responsive: `kubectl get nodes` should show your node as `Ready`.

### Step 1 — Prerequisites (Node Prep)
Ensure your Master Node has Docker, Helm, and the required Longhorn storage dependencies installed before building the cluster.
```bash
# 1. Install Docker & Storage Dependencies
sudo apt update && sudo apt install -y docker.io open-iscsi nfs-common cryptsetup linux-modules-extra-$(uname -r)
sudo usermod -aG docker $USER && newgrp docker

# 2. Secure Storage: Mask Multipathd & Load Kernel Modules
sudo systemctl stop multipathd.socket multipathd.service 2>/dev/null || true
sudo systemctl disable multipathd.socket multipathd.service 2>/dev/null || true
sudo systemctl mask multipathd.socket multipathd.service 2>/dev/null || true
sudo modprobe iscsi_tcp
sudo modprobe dm_crypt
echo "iscsi_tcp" | sudo tee -a /etc/modules
echo "dm_crypt" | sudo tee -a /etc/modules
sudo systemctl enable --now iscsid

# 3. Install Helm
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

### Step 3 — Install Cluster Operators
To power the SOC, we need to install five critical Kubernetes Operators. These act as "robot administrators" that manage storage, databases, autoscaling, and security automatically.

```bash
# 1. Install Longhorn (Distributed Block Storage)
helm repo add longhorn https://charts.longhorn.io && helm repo update
helm install longhorn longhorn/longhorn --namespace longhorn-system --create-namespace --set defaultSettings.defaultReplicaCount=1

# 2. Install Elastic ECK (Database Manager)
helm repo add elastic https://helm.elastic.co && helm repo update
helm upgrade --install elastic-operator elastic/eck-operator -n elastic-system --create-namespace

# 3. Install Cert-Manager (Automated TLS Certificates)
helm repo add jetstack https://charts.jetstack.io && helm repo update
helm install cert-manager jetstack/cert-manager --namespace cert-manager --create-namespace --set installCRDs=true

# 4. Install KEDA (Event-Driven Autoscaler for Kafka lag)
helm repo add kedacore https://kedacore.github.io/charts && helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace

# 5. Install VPA (Vertical Pod Autoscaler for CPU/RAM tuning)
git clone https://github.com/kubernetes/autoscaler.git /tmp/autoscaler
cd /tmp/autoscaler/vertical-pod-autoscaler
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml ./hack/vpa-up.sh
```

> **Note on Longhorn:** We set `defaultReplicaCount=1` for single-node setups. For multi-node clusters with redundancy, change this to `2` or `3` in the Longhorn UI.
> **Note on Autoscaling:** VPA runs silently in observation mode (view recommendations via `kubectl describe vpa -n tlsoc`). KEDA auto-scales based on the `kafka` settings in your `values.yaml`.

### Step 4 — Build the FOSS-SOC Engine Docker Image
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

### Step 5 — Configure & Deploy TLSOC via Helm
By default, the stack is perfectly configured to use the internal Kafka broker running inside Kubernetes. 

If you ever decide to use a dedicated external Kafka broker, simply open `helm/tlsoc/values.yaml` and update the `kafka.host` and `kafka.port` values. You no longer need to edit any YAML templates directly!

Deploy the entire stack:
```bash
helm install tlsoc ./helm/tlsoc
```

*Note: The `tlsoc-setup` Kubernetes Job will automatically wait for Elasticsearch and Kibana to become healthy, and then it will execute API calls to dynamically inject all Dashboards, SIEM Rules, and Data Views into Kibana.*

---

## 📡 Connecting Target Endpoints (rsyslog + omkafka)

To send logs from a target Linux machine (e.g., a web server or firewall) into your SOC, you do not need to install heavy agents like Filebeat. Instead, use the native `rsyslog` daemon.

**1. Install the Kafka Output Module**
```bash
sudo apt update && sudo apt install rsyslog-kafka
```

**2. Configure Rsyslog to Forward to Kafka**
Create a new configuration file on the target machine: `sudo nano /etc/rsyslog.d/99-kafka.conf`
```text
# Load the omkafka module
module(load="omkafka")

# Forward all auth logs (SSH, Sudo) to the 'linux_auth' Kafka topic
auth,authpriv.* action(
    type="omkafka"
    topic="linux_auth"
    broker=["<YOUR_K3S_MASTER_IP>:9094"]
    confParam=["security.protocol=PLAINTEXT"]
)
```

**3. Restart Rsyslog**
```bash
sudo systemctl restart rsyslog
```
Your target machine is now instantly shipping raw logs to the SOC!

---

## 🖥️ Scaling to Multiple Nodes (Multi-Laptop Cluster)

TLSOC is designed to start on a single laptop and seamlessly expand to a multi-node cluster. Stateless workloads (FOSS-Engine, Logstash) will automatically distribute across all connected laptops via KEDA.

### Architecture Overview
* **Master Node (Control Plane):** Runs K3s Server, Elasticsearch, Kafka, and Redis. These stateful services are pinned here via `nodeSelector` so their data stays on one disk.
* **Worker Nodes:** Run the lightweight, stateless workloads. Kubernetes automatically schedules FOSS-Engine and Logstash replicas onto these machines to share the parsing load.

### How `add-worker.sh` Works
To make joining nodes completely painless, we wrote `add-worker.sh`. It is an interactive script that runs on the new worker laptop. Under the hood, it:
1. **Prompts** for the Master Node's IP address and cluster join token.
2. **Auto-detects** the safest network IP (or VPN IP) to route traffic to the Master node by running `ip route get <MASTER_IP>`.
3. **Installs** critical storage dependencies required by Longhorn (`open-iscsi`, `nfs-common`, `cryptsetup`).
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
sudo apt-get install -y open-iscsi nfs-common cryptsetup
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
See [Step 3 — Install Cluster Operators](#step-3--install-cluster-operators) in the Installation Guide above.

### When to Use Longhorn
* **Multi-Node Laptop Clusters:** If you have 2+ nodes, Longhorn is mandatory. It ensures that if one laptop loses power, your databases survive. It provides high-speed Block-Level storage (iSCSI) required by databases.
* **Alongside Enterprise NFS:** Even if your datacenter already has a massive Enterprise NAS, Longhorn is still highly recommended. Databases (Elasticsearch, Kafka, Redis) corrupt easily on "File-Level" NFS storage. Longhorn provides the "Block-Level" translation databases demand, plus cloud-native snapshotting, cloning, and backup features that bare NFS lacks.

### When is Longhorn Redundant?
* **The Massive Singular Server:** If you deploy TLSOC in production on a massive, single bare-metal server (e.g., 64-cores, 1TB RAM), Longhorn adds unnecessary CPU overhead. There is no second machine to replicate to, so the replica feature is wasted.

#### How to Disable Longhorn (Single-Server Mode)

> **⚠️ PRECAUTION:** You must delete all existing Longhorn PVCs before switching to `local-path`. Switching the StorageClass of an existing PVC is impossible. This means all data in Longhorn volumes will be destroyed. Back up any critical data first.

1. **Uninstall Longhorn:**
   ```bash
   # First, delete all PVCs to release the Longhorn volumes
   kubectl delete pvc --all -n tlsoc
   
   # Uninstall Longhorn
   helm uninstall longhorn -n longhorn-system
   kubectl delete namespace longhorn-system
   ```
2. **Update Helm Templates:** Change StorageClass in **all** of these template files from `longhorn` to `local-path`:

   | File | What to Change |
   |---|---|
   | `helm/tlsoc/templates/foss-engine-statefulset.yaml` | Change `storageClassName: "longhorn"` → `"local-path"` (in the `volumeClaimTemplates` section) |
   | `helm/tlsoc/templates/kafka.yaml` | Change `storageClassName: "longhorn"` → `"local-path"` |
   | `helm/tlsoc/templates/redis.yaml` | Change `storageClassName: "longhorn"` → `"local-path"` |
   | `helm/tlsoc/templates/eck.yaml` | Change `storageClassName: longhorn` → `local-path` |

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

### 🛡️ Longhorn Storage: Potential Node Warnings & Solutions
When adding nodes, you might see red warning icons in the Longhorn UI under Node Conditions. Here is what they mean and how to fix them:

**1. `KernelModulesLoaded` is Red**
* **Issue:** Longhorn needs the `iscsi_tcp` module loaded into the kernel to attach virtual hard drives. Standard Ubuntu Desktop or certain cloud images often don't include this module by default. Furthermore, Longhorn can cache failures and ignore the module even after it's loaded.
* **Fix:** Install the extra modules package, load the module, and hard-restart Longhorn to force a rescan:
  ```bash
  sudo apt-get update && sudo apt-get install -y linux-modules-extra-$(uname -r)
  sudo modprobe iscsi_tcp
  sudo modprobe dm_crypt
  echo "iscsi_tcp" | sudo tee -a /etc/modules
  echo "dm_crypt" | sudo tee -a /etc/modules
  sudo systemctl restart iscsid
  sudo systemctl enable iscsid
  kubectl delete pod -n longhorn-system -l app=longhorn-manager
  ```

**2. `RequiredPackages` is Red**
* **Issue:** The node failed to install the iSCSI clients automatically (usually due to a locked `apt` process). Without iSCSI, our stack cannot mount the Longhorn block-storage drives.
* **Fix:** Run this on the affected node:
  ```bash
  sudo apt-get update && sudo apt-get install -y open-iscsi nfs-common cryptsetup
  sudo systemctl enable --now iscsid
  ```

**3. `Multipathd` is Red**
* **Issue:** A legacy background service called `multipathd` (used for physical enterprise SAN arrays) is running. If left active, it detects Longhorn's virtual drives and tries to manage them, causing a conflict that forces the drives into Read-Only mode and crashes our databases.
* **Fix:** Since K3s nodes use Software-Defined Storage rather than physical SANs, `multipathd` is completely obsolete. The most robust fix is to aggressively disable and mask the service so systemd cannot auto-restart it via sockets:
  ```bash
  sudo systemctl stop multipathd.socket multipathd.service
  sudo systemctl disable multipathd.socket multipathd.service
  sudo systemctl mask multipathd.socket multipathd.service
  kubectl delete pod -n longhorn-system -l app=longhorn-manager
  ```

### Key Longhorn Settings
| Setting | Recommended Value | What It Does |
|---|---|---|
| Default Replica Count | `1` (single-node) / `2` (multi-node) | Number of physical copies of each volume. |
| Default Data Locality | `best-effort` | Tries to keep data on the same node as the pod using it, maximizing read/write speed. |
| Automatic Salvage | `Enabled` | If all replicas go faulty (e.g., all nodes lost power simultaneously), Longhorn will try to recover the most intact replica automatically. |
| Auto Delete Workload Pod on Detach | `Enabled` | If a node crashes and the volume detaches, Kubernetes will auto-kill and respawn the pod on a healthy node where a backup replica exists. |
| Storage Over Provisioning % | `100` | Prevents Longhorn from promising more storage than physically exists. |
| Pod Deletion Policy When Node is Down | `delete-statefulset-pod` | Fixes the "Volumes stuck in Attaching" issue. Auto-kills stuck pods on crashed laptops so volumes can safely jump to a healthy node without human intervention. |

### Troubleshooting Longhorn
* **Volumes Stuck in "Degraded":** A node disconnected. Longhorn is warning you that it doesn't have enough physical copies. Reconnect the node, or delete the dead replica in the UI so Longhorn can rebuild it on a healthy node.
* **Volumes Stuck in "Attaching" (StatefulSet Split-Brain):** When a node crashes abruptly (e.g., a laptop loses Wi-Fi), Kubernetes StatefulSets will **never** automatically move the pod to a new node. This prevents a "split-brain" scenario where two nodes write to the same `ReadWriteOnce` volume and corrupt the database. The pod gets permanently stuck in `Terminating` on the dead node, and Longhorn gets stuck in `Attaching` because it cannot mount until the old pod is truly dead. 
  * *Fix:* Go to the Longhorn UI → **Settings** → **General**. Scroll down to **Pod Deletion Policy When Node is Down** and change it to `delete-statefulset-pod`. This automates the fix: Longhorn will realize the node is dead and trigger a force-delete of the stuck pod, allowing the volume to instantly and safely jump to a healthy node.
* **Volumes Stuck in "Detached":** Ensure the Pod is actively scheduled. If a node crashes, enable the setting `Automatically Delete Workload Pod when Volume Detaches` so Kubernetes forces the Pod to respawn on a healthy node where the backup replica lives.
* **"Scheduling Failure" on a Volume:** The remaining nodes don't have enough free storage. Either free up space or add another node.
* **Red warnings for `KernelModulesLoaded` or `RequiredPackages`:** Install the missing dependencies: `sudo apt-get install -y open-iscsi nfs-common`. If the node still shows the warning but the overall status is `Ready`, Longhorn is falling back to basic block storage and will still work.

---

## ⚖️ Autoscaling Deep Dive (KEDA & VPA)

TLSOC uses two complementary autoscaling technologies. Each solves a different problem.

### KEDA — Kubernetes Event-Driven Autoscaling (for FOSS-Engine)

KEDA connects directly to your Kafka broker and counts how many messages are sitting unread in the consumer group (`foss-soc-engine`). When lag exceeds the threshold, KEDA adds FOSS-Engine pods. When the backlog clears, it scales back down. This is far more precise than CPU — a log flood triggers scaling before CPU even spikes.

```bash
# See KEDA ScaledObject status
kubectl get scaledobject -n tlsoc

# See detailed KEDA events (connection status, scaling decisions)
kubectl describe scaledobject foss-engine-scaler -n tlsoc

# See the internal HPA KEDA manages behind the scenes
kubectl get hpa -n tlsoc
```

| Setting in `keda.yaml` | Default | Effect |
|---|---|---|
| `minReplicaCount` | `1` | Minimum FOSS-Engine pods. Set to `0` to scale to zero when no logs flow. |
| `maxReplicaCount` | `3` | Maximum pods. **Must match your Kafka partition count** for true parallelism. |
| `lagThreshold` | `100` | Unread messages per partition that triggers a new replica. Lower = faster scaling. |
| `kafka.host` | `kafka.tlsoc.svc.cluster.local` | Internal DNS or external IP of your Kafka broker. |
| `kafka.port` | `9092` | Port of your Kafka broker. |
| `consumerGroup` | `foss-soc-engine` | Must match `group_id` in `configmaps.yaml`. |
| `cooldownPeriod` | `120` | Seconds to wait before scaling down. Only relevant if `minReplicaCount=0`. |

> **⚠️ Critical:** For KEDA to scale FOSS-Engine pods **meaningfully**, your Kafka topics must have **multiple partitions**. Kafka assigns one partition per consumer — if a topic has 1 partition, only 1 pod will ever receive messages, even if 3 are running. Run this on your Kafka machine to increase partitions:
> ```bash
> kafka-topics.sh --alter --topic summersoc --partitions 3 --bootstrap-server localhost:9092
> kafka-topics.sh --alter --topic webserver --partitions 3 --bootstrap-server localhost:9092
> kafka-topics.sh --alter --topic mailserver --partitions 3 --bootstrap-server localhost:9092
> ```

### VPA — Vertical Pod Autoscaler (for All 6 Services)

VPA runs in `updateMode: "Off"` (observation only). It watches every service and generates the mathematically ideal CPU and RAM values based on real historical usage. It never restarts your pods — it only gives you recommendations to act on manually.

```bash
# View recommendations for ALL services at once
kubectl describe vpa -n tlsoc

# View recommendations for a specific service
kubectl describe vpa elasticsearch-vpa -n tlsoc
kubectl describe vpa kafka-vpa -n tlsoc
kubectl describe vpa logstash-vpa -n tlsoc
kubectl describe vpa foss-engine-vpa -n tlsoc
kubectl describe vpa redis-vpa -n tlsoc
kubectl describe vpa kibana-vpa -n tlsoc

# List all VPAs and their current status
kubectl get vpa -n tlsoc
```

In the output, look for the `Recommendation` section:
```
Recommendation:
  Container Recommendations:
    Container Name: elasticsearch
      Target:      cpu: 163m    memory: 2281Mi   ← Use these values in values.yaml
      Lower Bound: cpu: 25m     memory: 1500Mi   ← Bare minimum to survive
      Upper Bound: cpu: 500m    memory: 4Gi      ← Peak under heavy load
```

**How to apply VPA recommendations:**
1. Run `kubectl describe vpa <service>-vpa -n tlsoc` and note the **Target** values.
2. Open `helm/tlsoc/values.yaml` and update the `requests` fields to match the Target.
3. Set `limits` to ~30-50% above `requests` (never equal — limits need headroom).
4. Run `helm upgrade tlsoc ./helm/tlsoc` to apply.
5. Wait another week and repeat — VPA gets more accurate over time.

> **⚠️ VPA configuration:** Set VPA to `updateMode: "Off"` for all services initially, until you trust its recommendations.
> ```yaml
> resourcePolicy:
>   containerPolicies:
>   - containerName: logstash
>     controlledResources: ["memory"]
> ```

---

## 🔧 Configurable Settings Reference

All the settings you can tune across the stack, organized by component.

### Application Resources — `helm/tlsoc/values.yaml`

This is your primary control panel. Edit these values and run `helm upgrade tlsoc ./helm/tlsoc` to apply any change live.

```yaml
elasticsearch:
  resources:
    requests:
      memory: "1Gi"    # Minimum memory reserved for ES. Raise this if ES keeps OOM-crashing.
      cpu: "500m"      # 500m = 0.5 CPU cores. VPA currently recommends ~163m.
    limits:
      memory: "2Gi"    # Maximum memory ES can ever use. Should be ~30% above requests.
      cpu: "1000m"     # 1 full CPU core maximum.
  storage: "20Gi"      # Longhorn PVC size per ES node. Cannot shrink after creation.

kibana:
  resources:
    requests: { memory: "1Gi", cpu: "200m" }
    limits:   { memory: "2Gi", cpu: "500m" }

logstash:
  # Logstash runs as a sidecar to FOSS-Engine, so it shares the same replica count.
  resources:
    requests: { memory: "512Mi", cpu: "200m" }  # VPA recommends ~78m CPU, ~1.2Gi memory.
    limits:   { memory: "1Gi",   cpu: "500m" }  # ⚠️ VPA says memory limit is too tight — raise to 1.5Gi.

foss_engine:
  replicas: 1          # Starting replicas. KEDA will scale this up/down automatically.
  resources:
    requests: { memory: "256Mi", cpu: "100m" }  # VPA recommends 25m CPU, 250Mi memory.
    limits:   { memory: "512Mi", cpu: "250m" }

kafka:
  replicas: 1          # Do NOT change — Kafka multi-node requires manual partition rebalancing.

redis:
  replicas: 1          # Do NOT change — Redis is a single-instance cache. Multi-node requires Redis Cluster mode.
```

### Autoscaling Settings


**KEDA (`helm/tlsoc/templates/keda.yaml`):**

| Setting | Default | Tune It When... |
|---|---|---|
| `minReplicaCount` | `1` | Set to `0` to save resources when no logs are flowing (KEDA will wake it up in ~15 seconds). |
| `maxReplicaCount` | `3` | Set equal to your Kafka topic partition count for true parallelism. |
| `lagThreshold` | `100` | Lower to `20` for faster reaction to log bursts. Raise to `500` to batch more before scaling. |
| `kafka.host / kafka.port` | `kafka.tlsoc.svc.cluster.local:9092` | Central configuration to point the entire stack to any Kafka broker. |

**VPA (`helm/tlsoc/templates/vpa.yaml`):**

| Setting | Default | Tune It When... |
|---|---|---|
| `updateMode` | `"Off"` | Change to `"Auto"` for stateless services (foss-engine, logstash) once you trust VPA's recommendations. Never use `"Auto"` for databases. |

Add resource bounds to prevent VPA from recommending extreme values:
```yaml
spec:
  resourcePolicy:
    containerPolicies:
    - containerName: elasticsearch
      minAllowed: { cpu: "100m", memory: "1Gi" }  # VPA won't recommend below this
      maxAllowed: { cpu: "2000m", memory: "4Gi" }  # VPA won't recommend above this
```

### Longhorn Settings (via Longhorn UI)

Access the UI: `kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80` → open `http://localhost:8080`

| Setting | Recommended | Where |
|---|---|---|
| Default Replica Count | `1` (single-node) / `2` (multi-node) | Settings → General |
| Default Data Locality | `best-effort` | Settings → General — keeps data on same node as pod |
| Replica Auto Balance | `best-effort` | Settings → General — auto-rebalances when nodes join/leave |
| Automatic Salvage | `Enabled` | Settings → General — recovers volumes if all nodes crash simultaneously |
| Storage Over Provisioning % | `100` | Settings → General — prevents over-committing disk |
| Storage Minimal Available % | `25` | Settings → General — stops new volumes if disk is 75%+ full |

### Elasticsearch Node Scaling

Elasticsearch is managed by the ECK Operator. To add nodes, edit `helm/tlsoc/templates/elastic.yaml`:
```yaml
nodeSets:
- name: default
  count: 3   # Change from 1 to 3 for a proper HA cluster
```
ECK handles shard rebalancing automatically. Each node gets its own Longhorn PVC. Run `helm upgrade` to apply.

> **⚠️ Prerequisite:** Each ES node needs ~2GB RAM. 3 nodes = ~6GB RAM just for Elasticsearch. Ensure your machine can handle it before scaling.

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

### Accessing the Parsed Logs (StatefulSet Volumes)
Because FOSS-Engine runs as a StatefulSet with isolated ReadWriteOnce volumes, you must `exec` into a specific pod to see its parsed JSON files:
```bash
# View parsed logs inside pod 0
kubectl exec -it foss-engine-0 -n tlsoc -c foss-engine -- tail -f /parser_output/webserver.json

# View parsed logs inside pod 1 (if KEDA has scaled up)
kubectl exec -it foss-engine-1 -n tlsoc -c foss-engine -- tail -f /parser_output/webserver.json
```

### Storage Maintenance
```bash
# Check PVC status (you should see one PVC per FOSS-Engine pod)
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

# See CPU and RAM usage of all pods (requires metrics-server)
kubectl top pods -n tlsoc

# Check KEDA ScaledObject status (is it connected to Kafka?)
kubectl get scaledobject -n tlsoc

# Full KEDA details: connection status, lag values, scaling events
kubectl describe scaledobject foss-engine-scaler -n tlsoc

# Check VPA recommendation status for all services
kubectl get vpa -n tlsoc

# Read VPA resource recommendations for a specific service
kubectl describe vpa elasticsearch-vpa -n tlsoc
kubectl describe vpa logstash-vpa -n tlsoc
kubectl describe vpa foss-engine-vpa -n tlsoc
```

### Advanced Operations (Rollbacks & DNS)
```bash
# Instantly rollback a deployment if an update breaks it (e.g., Logstash crashing)
kubectl rollout undo deployment logstash -n tlsoc

# Spin up a temporary Ubuntu pod to test internal DNS resolution (CoreDNS)
kubectl run -i --tty --rm debug --image=ubuntu --restart=Never -n tlsoc -- sh -c "apt update && apt install dnsutils -y && nslookup kafka.tlsoc.svc.cluster.local"
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
# Manually force FOSS-Engine to 3 replicas (KEDA will override this within ~30 seconds)
kubectl scale statefulset foss-engine -n tlsoc --replicas=3

# Permanently change min/max replicas — edit values.yaml and upgrade:
helm upgrade tlsoc ./helm/tlsoc

# Pause KEDA autoscaling temporarily (KEDA will stop managing replicas)
kubectl annotate scaledobject foss-engine-scaler -n tlsoc autoscaling.keda.sh/paused=true

# Resume KEDA autoscaling
kubectl annotate scaledobject foss-engine-scaler -n tlsoc autoscaling.keda.sh/paused-

# Check Kafka consumer lag on your external broker (run on the Kafka machine)
kafka-consumer-groups.sh --describe --group foss-soc-engine --bootstrap-server localhost:9092
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

### 🔍 Advanced Distributed Log Searching (`search_logs.sh`)
Because the FOSS-Engine uses KEDA to horizontally auto-scale based on Kafka traffic, you might have engine pods that process a bunch of logs and then scale down to zero when idle. If a security analyst wants to search raw output files for a specific IP address, standard `kubectl exec` is useless because the pod might be sleeping and the Longhorn volume is detached.

We wrote `search_logs.sh` to solve this! This script is a powerful SOC debugging tool that can search *across all FOSS-Engine volumes simultaneously*. 
1. If the engine pod is active, it runs a live regex search inside it.
2. If the engine pod is scaled down (sleeping), it automatically spins up a temporary debugger pod, attaches the sleeping Longhorn volume, searches it, and then cleanly destroys the debugger pod.

**Usage:**
```bash
# Search for a specific IP address
./search_logs.sh '192.168.1.100'

# Use regex to search for multiple patterns
./search_logs.sh 'postfix|failed password|root'
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

### 3. Pods stuck in `Terminating` forever
**Why:** A node hard-crashed or the storage volume got forcefully detached.
**The Fix:** Force delete the pod:
```bash
kubectl delete pod <pod-name> -n tlsoc --grace-period=0 --force
```

### 4. FOSS-Engine pod in `ErrImageNeverPull` or `ImagePullBackOff`
**Why:** The `foss-soc-engine:latest` Docker image is not on Docker Hub. It must be built locally on every node that runs the engine. The image was either not built or not imported into K3s.
**The Fix:**
```bash
sudo docker build -t foss-soc-engine:latest ./engine
sudo docker save foss-soc-engine:latest | sudo k3s ctr images import -
```

### 5. FOSS-Engine not parsing logs / "No Kafka brokers available"
**Why:** The bootstrap server in the engine's ConfigMap doesn't match your Kafka deployment, or Kafka hasn't finished starting.
**The Fix:**
1. Verify Kafka is running: `kubectl get pods -l app=kafka -n tlsoc`
2. Check the engine ConfigMap: `kubectl get configmap engine-config -n tlsoc -o yaml`. The `bootstrap_servers` should be `kafka.tlsoc.svc.cluster.local:9092` for in-cluster communication.
3. Restart the engine: `kubectl rollout restart statefulset foss-engine -n tlsoc`

### 6. Logstash not shipping logs to Elasticsearch
**Why:** Logstash can't reach Elasticsearch, or the Elastic password changed.
**The Fix:**
1. Check Logstash logs (running as a sidecar inside the engine pod): `kubectl logs foss-engine-0 -c logstash -n tlsoc --tail=100`
2. Verify the ES secret exists: `kubectl get secret tlsoc-es-elastic-user -n tlsoc`
3. Restart the pod to reset Logstash: `kubectl rollout restart statefulset foss-engine -n tlsoc`

### 7. Elasticsearch pod stuck in `CrashLoopBackOff`
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

### 8. Kafka pod in `CrashLoopBackOff`
**Why:** Usually caused by corrupted KRaft metadata or a Kafka version mismatch after upgrade.
**The Fix:**
```bash
# Check Kafka logs
kubectl logs kafka-0 -n tlsoc --tail=100

# Nuclear option: delete Kafka data and restart
kubectl delete pvc kafka-data-kafka-0 -n tlsoc
kubectl delete pod kafka-0 -n tlsoc
```



### 9. PVC stuck in `Pending` state
**Why:** No StorageClass can satisfy the claim, or Longhorn is not installed.
**The Fix:**
```bash
# Check available StorageClasses
kubectl get storageclass

# If "longhorn" is missing, install Longhorn (see Step 3)
# If using local-path, ensure the PVC uses storageClassName: "local-path"
kubectl describe pvc <pvc-name> -n tlsoc
```

### 10. KEDA shows `error describing topics: kafka server: topic does not exist`
**Why:** This is expected and harmless. Kafka only creates a topic the moment the first log is sent to it. If no logs are flowing, the topic doesn't exist yet, so KEDA can't measure lag on it. It will resolve automatically when your target machines start sending logs.
**It is NOT an error** — just a warning that KEDA is idle.

### 11. KEDA ScaledObject shows `Ready: False` / `TriggerError`
**Why:** KEDA cannot connect to the Kafka broker defined in your `values.yaml`.
**Solution:**
1. If using the internal default, ensure the `kafka` statefulset is running.
2. If connecting to an external broker, verify `kafka.host` in `values.yaml` is correct.
3. Verify network connectivity from the K3s node to your external broker:
```bash
nc -zv <YOUR_EXTERNAL_KAFKA_IP> 9094
```
**Check KEDA logs for details:**
```bash
kubectl logs -l app=keda-operator -n keda --tail=50
```

### 14. KEDA scales to 3 FOSS-Engine pods but log processing doesn't speed up
**Why:** Your Kafka topics only have 1 partition. Kafka assigns partitions to consumers 1:1. With 1 partition, only 1 pod can receive messages regardless of how many pods exist.
**The Fix:** Increase partitions on your Kafka broker machine:
```bash
# Run on the Kafka machine (not the K3s master)
kafka-topics.sh --alter --topic summersoc --partitions 3 --bootstrap-server localhost:9092
kafka-topics.sh --alter --topic webserver --partitions 3 --bootstrap-server localhost:9092
kafka-topics.sh --alter --topic mailserver --partitions 3 --bootstrap-server localhost:9092
kafka-topics.sh --alter --topic vulnerability --partitions 3 --bootstrap-server localhost:9092

# Verify partitions were updated
kafka-topics.sh --describe --topic summersoc --bootstrap-server localhost:9092
```
> **Note:** You cannot decrease partition count after setting it. Only increase.

### 15. VPA not generating recommendations after 15+ minutes
**Why:** VPA needs live metric data from the Metrics Server to generate recommendations. If the Metrics Server is down, or the pod has been restarted very recently, VPA has no data.
**The Fix:**
```bash
# Check VPA recommender logs for errors
kubectl logs -l app=vpa-recommender -n kube-system --tail=50

# Ensure the Metrics Server is running and returning data
kubectl top pods -n tlsoc

# Check the raw VPA output — look for the Recommendation section
kubectl describe vpa elasticsearch-vpa -n tlsoc
```

### 16. Elasticsearch fills its disk and stops indexing
**Why:** By default, Elasticsearch enters read-only mode when the disk reaches 95% full. New logs from Logstash will be rejected.
**The Fix:**
```bash
# Check how full the ES PVC is
kubectl exec -n tlsoc tlsoc-es-default-0 -- df -h /usr/share/elasticsearch/data

# Option 1: Delete old indices in Kibana → Stack Management → Index Management
# Option 2: Temporarily unlock read-only mode:
kubectl exec -n tlsoc tlsoc-es-default-0 -- curl -s -u elastic:$PASSWORD \
  -X PUT "https://localhost:9200/_settings" \
  -H 'Content-Type: application/json' \
  -d '{"index.blocks.read_only_allow_delete": null}' --insecure

# Option 3: Expand the PVC storage in values.yaml and run helm upgrade
# (Longhorn supports online PVC expansion without downtime)
```

### 17. After upgrading Elasticsearch (ECK), Logstash stops shipping logs
**Why:** ECK rotates internal TLS certificates when the Elasticsearch cluster is recreated or upgraded. Logstash's pod still holds the old CA certificate in memory.
**The Fix:** Roll the Logstash deployment to force it to mount the new certificate:
```bash
kubectl rollout restart deployment logstash -n tlsoc

# If Logstash is still not shipping (missed logs during downtime), wipe its position tracker:
kubectl exec -n tlsoc deploy/logstash -- find /usr/share/logstash/state -name '.sincedb*' -delete
kubectl rollout restart deployment logstash -n tlsoc
```

---

## 👁️ Visualizing the Cluster with OpenLens

Instead of relying entirely on terminal commands, you can use **OpenLens** (a powerful, open-source desktop GUI for Kubernetes) to visually manage your SOC cluster.

### What it does:
OpenLens allows you to graphically see all your pods, view live scrolling logs, access terminal shells inside containers, and monitor CPU/RAM usage across your FOSS-Engine and Logstash deployments in real-time.

### How to Install and Connect OpenLens on Ubuntu:
1. Download the OpenLens AppImage for Linux from the official GitHub releases.
2. Make it executable and run it:
```bash
chmod +x OpenLens-*.AppImage
./OpenLens-*.AppImage
```
3. OpenLens will automatically detect your K3s configuration if it is located at `~/.kube/config` (as set up in Step 2 of the Installation Guide).
4. You can now visually monitor the cluster, scale deployments, and view live logs directly from the OpenLens GUI.

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

#### Stage 4: Enrichment (GeoIP)
If the log contains a public IP address (`source.ip`), the Engine performs an ultra-fast local lookup against a **MaxMind GeoLite2** database (`database/GeoLite2-City.mmdb`). It attaches Country, City, and Latitude/Longitude coordinates to the log.

#### Stage 5: Output (Batched File Drop)
Once the log is a perfectly formatted JSON object, the Engine batches up to **1000 logs** (or waits **5 seconds**, whichever comes first) and drops them as a single `.json` file into the `/parser_output/` directory.

### StatefulSet & RWO Storage Architecture (The Fix for NFS Locks)
To prevent Linux `inotify` file-watching bugs associated with NFS (ReadWriteMany) network drives, the FOSS-Engine is deployed as a **StatefulSet**. This guarantees that every single FOSS-Engine pod gets its own dedicated, isolated **Longhorn ReadWriteOnce (RWO)** hard drive (`/parser_output`). Because the drive is strictly attached to one pod, Logstash can instantly and reliably detect file changes using native block-storage events.

### Zero-Trust Architecture
Logstash operates under a strict Zero-Trust model to protect parsed data:
* The `/parser_output` volume (containing the critical JSON logs) is mounted strictly as `readOnly: true` to Logstash. 
* Logstash's internal file-tracking database (`.sincedb`) is persisted to its own isolated `logstash-state` volume.

This ensures that if Logstash is ever compromised, attackers cannot modify or delete the original parsed logs.

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
│   └── geoip.py         # GeoIP lookup logic
└── rules/
    ├── apache_access.yaml   # Apache/Nginx access log parser
    ├── linux_auth.yaml      # SSH, Sudo, Su authentication parser
    ├── modsec.yaml          # ModSecurity WAF alert parser
    ├── postfix.yaml         # Postfix mail relay parser
    └── roundcube.yaml       # Roundcube webmail login parser
```

### Building the Image
See [Step 4 — Build the FOSS-SOC Engine Docker Image](#step-4--build-the-foss-soc-engine-docker-image) in the Installation Guide above.

### Key Configuration (ConfigMap vs config.yaml)
The `engine/config.yaml` file is for **local development only**. When running inside Kubernetes, the engine's config is overridden by the `engine-config` ConfigMap defined in `helm/tlsoc/templates/configmaps.yaml`. This ConfigMap sets:
* `bootstrap_servers` to the internal Kubernetes DNS name (`kafka.tlsoc.svc.cluster.local:9092`) instead of an external IP.
* `group_id` to `foss-soc-engine` (the Kubernetes consumer group).
* `redis.host` to `redis.tlsoc.svc.cluster.local`.

If you need to change the Kafka bootstrap server, edit the ConfigMap in `configmaps.yaml`, not the `engine/config.yaml` file. Then run `helm upgrade`.

### Security Model
The Dockerfile enforces a non-root security model. The engine runs as `appuser` (UID 1001), not root. This ensures that even if an attacker exploits a vulnerability in the Python code, they cannot escape the container or modify system files.
