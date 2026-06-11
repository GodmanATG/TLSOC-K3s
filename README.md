# TLSOC K3s — Next-Gen Security Operations Center

Welcome to the TLSOC K3s deployment repository. This project represents a massive architectural evolution of a Security Operations Center (SOC) stack, migrating from fragile Docker Compose scripts to a highly resilient, auto-scaling, and self-healing Kubernetes architecture using **Helm** and the **Elastic Cloud on Kubernetes (ECK) Operator**.

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
| **ECK Operator** | Database Management | The official Elastic Kubernetes Operator automatically handles Elasticsearch cluster formation, auto-healing dead nodes, generating secure passwords, and provisioning internal TLS certificates seamlessly. |
| **Kafka & Redis** | Data Brokering | Kafka acts as an ultra-fast shock absorber. If 50,000 logs arrive instantly during a DDoS attack, Kafka holds them in a queue so the FOSS-Engine isn't overwhelmed. Redis handles high-speed caching for IP Geolocation. |
| **Traefik** | Ingress Routing | Replaces `port-forwarding`. Traefik intercepts incoming web traffic, cleanly terminates HTTPS SSL certificates, and routes you directly to Kibana at `kibana.tlsoc.local`. |

---

## 💻 Hardware Requirements

Because Elasticsearch and Java applications require significant memory, we have tuned the default `values.yaml` limits to run on a standard developer laptop without crashing.

### Minimum Specs (Development & Testing)
* **OS:** Linux (Ubuntu 22.04+ recommended) or WSL2
* **RAM:** 8 GB (Elasticsearch requires a minimum 2GB heap, plus OS overhead)
* **CPU:** 4 Cores
* **Storage:** 20 GB free space (Local-path provisioner uses standard disk space)

### Recommended Specs (Production & High Availability)
* **RAM:** 16 GB+
* **CPU:** 8 Cores
* **Storage:** 50 GB+ SSD
* **Network:** Static IP for external Log Producers to reach Kafka.

---

## 🚀 Installation Guide

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

### Step 3 — Install the Elastic ECK Operator
Before deploying the SOC, you must install the Elastic Operator. This "robot administrator" will automatically build and secure your databases.
```bash
helm repo add elastic https://helm.elastic.co
helm repo update
helm upgrade --install elastic-operator elastic/eck-operator -n elastic-system --create-namespace
```

### Step 4 — Deploy TLSOC via Helm
```bash
# Clone the repository
git clone https://github.com/GodmanATG/TLSOC-K3s
cd TLSOC-K3s

# Deploy the entire stack using Helm
helm install tlsoc ./helm/tlsoc
```

*Note: The `tlsoc-setup` Kubernetes Job will automatically wait for Elasticsearch and Kibana to become healthy, and then it will execute API calls to dynamically inject all Dashboards, SIEM Rules, and Data Views into Kibana.*

---

## 🌐 Accessing the SOC (Kibana)

You do not need to port-forward! Traefik handles the routing locally.
Open your browser and navigate to: **`https://localhost`** or **`https://kibana.tlsoc.local`**
*(Note: Since the SSL certificate is self-signed, you will need to bypass the Chrome security warning).*

**How to get your auto-generated Password:**
The ECK Operator generates a highly secure random password on every fresh install. Fetch it via:
```bash
kubectl get secret tlsoc-es-elastic-user -n default -o jsonpath='{.data.elastic}' | base64 -d
```
* **Username:** `elastic`
* **Password:** *(Output from the command above)*

---

## 📖 Operational Cheat Sheet

### Upgrading Configurations
If you edit `helm/tlsoc/values.yaml` (e.g., to increase replicas or RAM), apply the changes live without deleting data:
```bash
helm upgrade tlsoc ./helm/tlsoc
```

### Checking Cluster Status
```bash
# See all running services and pods
kubectl get pods

# See Persistent Storage Volumes (Hard drives)
kubectl get pvc
```

### Viewing Logs
```bash
# Watch the FOSS-Engine parse logs in real-time
kubectl logs -l app=foss-engine -f --tail=50

# Watch Logstash pipeline logs
kubectl logs -l app=logstash -f --tail=50
```

### Scaling Up
The architecture relies on the **Horizontal Pod Autoscaler (HPA)**. 
However, to manually define the minimum replica baseline, edit `values.yaml`:
```yaml
foss_engine:
  replicas: 3
```
Then run the `helm upgrade` command above. The replicas will instantly spin up and distribute across your worker nodes!

### Hard Reset (Wipe Everything)
If you want to completely erase the database and start from zero:
```bash
helm uninstall tlsoc
kubectl delete pvc --all
# Wait 30 seconds, then reinstall:
helm install tlsoc ./helm/tlsoc
```
---

## 🖥️ Scaling to Multiple Nodes (Multi-Laptop Cluster)

TLSOC is designed to start on a single laptop and seamlessly expand to a multi-node cluster when you need more processing power. Stateless workloads (FOSS-Engine, Logstash, Kibana) will automatically distribute across all connected laptops.

### How it works
* **Laptop 1 (Control Plane):** Runs the K3s server, Elasticsearch, Kafka, and Redis. These stateful services are pinned here so their data stays on one disk.
* **Laptop 2, 3, ... (Workers):** Run only the lightweight, stateless workloads. Kubernetes automatically schedules FOSS-Engine and Logstash replicas onto these machines to share the parsing load.

### Step 1 — Get the Join Credentials (on Laptop 1)
```bash
# Get the join token
sudo cat /var/lib/rancher/k3s/server/node-token

# Get the control plane IP
hostname -I | awk '{print $1}'
```

### Step 2 — Join the Worker (on Laptop 2/3)
Copy the `add-worker.sh` script to the new laptop and run:
```bash
bash add-worker.sh <LAPTOP1_IP> <TOKEN>
```
This installs K3s in **agent mode** on the new laptop and automatically registers it with your cluster.

### Step 3 — Verify (on Laptop 1)
```bash
kubectl get nodes
```
You should see all laptops listed with `STATUS: Ready`. Kubernetes will now automatically distribute Engine and Logstash pods across all available nodes when scaling is triggered by the HPA or by increasing replicas in `values.yaml`.

---

## 🐛 Troubleshooting & Common Bugs

### 1. `kubectl: command not found`
**Why:** Standard Kubernetes distributions install a standalone `kubectl` binary, but K3s bundles it inside the `k3s` command.
**The Fix:** You can either prefix all your commands with `k3s` (e.g., `k3s kubectl get pods`), or create a symlink so `kubectl` works natively:
```bash
sudo ln -s /usr/local/bin/k3s /usr/local/bin/kubectl
```

### 2. Sudo Permission Denied (`localhost:8080 was refused`)
**Why:** K3s locks the cluster configuration file (`/etc/rancher/k3s/k3s.yaml`) to the `root` user by default for security. If you try to run `kubectl get pods` as a normal user, it will fail to read the file and look for a default cluster on port 8080.
**The Fix:** You must copy the config to your home directory and take ownership of it:
```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(whoami):$(whoami) ~/.kube/config
export KUBECONFIG=~/.kube/config
```

### 3. WSL2 Crash: `Failed to start ContainerManager...`
**Why:** If you are running K3s inside Windows Subsystem for Linux (WSL2), the way Windows handles Linux Control Groups (`cgroups`) differs from a native Linux kernel. K3s gets confused when it tries to read the system mounts.
**The Fix:** You need to explicitly tell K3s to use the `systemd` cgroup driver when starting the cluster. Instead of a normal start, run:
```bash
sudo k3s server --kubelet-arg="--cgroup-driver=systemd"
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
*(Replace `<YOUR_WSL_DISTRO>` with your WSL distro name (e.g., `Ubuntu-24.04`) and `<YOUR_WINDOWS_USERNAME>` with your Windows username).*
3. Open the **OpenLens** application on Windows. It will automatically read your `.kube/config` file and connect to the TLSOC cluster!

---

## 🐍 The FOSS-SOC Engine (Deep Dive)

The **FOSS-Engine** is the custom-built, ultra-fast core of the TLSOC architecture. While standard SOCs use bulky, slow log parsers (like native Logstash or Fluentd) to do heavy string manipulation, TLSOC pushes all of the complex log normalization, regex parsing, and threat intelligence enrichment into this specialized, lightweight Python application.

### What exactly does it do?
Security logs generated by firewalls, web servers, and mail servers come in completely different formats. An Apache web server log looks nothing like a ModSecurity firewall log. If you feed raw, chaotic text into Elasticsearch, your Kibana Dashboards will break because they won't know where to find the "IP Address" or the "Username."

**The FOSS-Engine solves this problem.** It acts as a massive universal translator:
1. It connects to **Kafka** and subscribes to multiple log streams (topics).
2. It ingests the chaotic raw text logs.
3. It maps the log to a specific **Parser Rule** (e.g., `apache_access.yml`, `linux_auth.yml`).
4. It extracts critical fields using Regex.
5. It standardizes the fields to match the **Elastic Common Schema (ECS)** (e.g., ensuring an IP address is always called `source.ip`).
6. It performs extremely fast **Data Enrichment** (converting the IP address into a Latitude/Longitude map coordinate).
7. It outputs perfectly clean, structured JSON files ready for instant indexing.

### How It Works (The Pipeline)

#### 1. Ingestion (Kafka Consumer)
The Engine operates as a high-throughput **Kafka Consumer Group**. It connects to the Kafka broker defined in `configmaps.yaml`. Because it uses a Kafka Consumer Group (`group_id: foss-soc-engine`), multiple engine pods can run concurrently, perfectly splitting the log parsing workload without duplicating logs.

#### 2. Rule Mapping (`program_mapping`)
When a log arrives, it has a metadata tag (e.g., `waf-nginx-access`). The Engine reads the `program_mapping` section of its `config.yaml` to determine which rule file to use:
```yaml
program_mapping:
  waf-nginx-access: "apache_access"
```

#### 3. Parsing (Regex & Normalization)
The Engine opens the corresponding `.yml` rule file in the `rules/` directory. These files contain highly optimized Regular Expressions. For example, it might extract the word `Failed password` from a Linux auth log and categorize it under `event.outcome: failure` and `event.category: authentication`.

#### 4. Enrichment (GeoIP & Threat Intel)
If the log contains a public IP address (`source.ip`), the Engine performs an ultra-fast local lookup against a **MaxMind GeoLite2** database. It attaches Country, City, and Coordinates. For extreme speed, it uses **Redis** to cache IPs it has already looked up.

#### 5. Output (File Drop)
Once the log is a perfectly formatted JSON object, the Engine drops it into the `/var/log/soc_output/` directory, which Logstash then securely reads and pipes into Elasticsearch.
