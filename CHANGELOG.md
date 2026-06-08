# Changelog: TLSOC-Aryan (Docker Compose) to TLSOC-K3s (Kubernetes)

This document details every architectural and configuration change made to migrate the FOSS SOC Engine from a single-machine Docker Compose environment to a highly scalable, multi-node Kubernetes (K3s) cluster.

## 1. Workload Organization (Deployments vs. StatefulSets)
*   **Docker Compose:** All containers were defined simply as "services". 
*   **Kubernetes:** We separated services based on their statefulness:
    *   **StatefulSets:** Elasticsearch, Kafka, and Redis. These require persistent, reliable storage and stable network identities. They are pinned to the control-plane node (Laptop 1) to ensure they always have access to their local disk volumes.
    *   **Deployments:** Kibana, Logstash, and the FOSS Engine. These are stateless applications. They can safely crash, restart, and automatically distribute themselves across all 3 laptops in the cluster to share the processing load.

## 2. Configuration & Secrets Management
*   **Docker Compose:** Used a `.env` file and direct file mounts for configuration files (like `logstash.conf`).
*   **Kubernetes:** 
    *   **ConfigMaps (`02-configmaps.yaml`):** Replaced file mounts. The Engine's `config.yaml`, Logstash's pipeline config, and Kafka's server properties are now natively embedded in the cluster configuration. This allows the configuration to instantly sync to all 3 laptops without copying files.
    *   **Secrets (`01-secrets.yaml`):** Replaced the `.env` file. Passwords and TLS certificates are securely base64 encoded and injected directly into the pods (as environment variables or mounted certificate files) by Kubernetes. 

## 3. Persistent Storage (Volumes to PVCs)
*   **Docker Compose:** Used local Docker volumes and host-path mounts.
*   **Kubernetes:** Uses Persistent Volume Claims (PVCs) backed by K3s's default `local-path` StorageClass.
    *   **Shared PVC (`06-shared-pvc.yaml`):** Created a shared storage space that allows the FOSS Engine to write parsed JSON files, and Logstash to instantly read and forward them to Elasticsearch, even if the pods restart.

## 4. Networking & DNS
*   **Docker Compose:** Services communicated using simple container names (e.g., `elasticsearch`).
*   **Kubernetes:** Uses CoreDNS.
    *   **Internal DNS:** Services communicate using Fully Qualified Domain Names (e.g., `elasticsearch.tlsoc.svc.cluster.local`). 
    *   **TLS Certificate Fix:** Because Kibana requires strict HTTPS verification, connecting to the long Kubernetes FQDN caused a mismatch with the `elasticsearch` SAN in the certificate. We fixed this by configuring Kibana and Logstash to connect using the short DNS name (`elasticsearch:9200`), perfectly matching the certificate.
    *   **External Access (NodePort):** Exposing Kafka to external log shippers on the network is no longer a simple `ports` array. We created a `NodePort` service (`30094`) which ensures that external laptops can send logs into the Kubernetes cluster from outside.

## 5. Automation of the Setup Script
*   **Docker Compose:** The setup script (which generates Kibana dashboards) ran as a standard container that slept and looped.
*   **Kubernetes (`10-setup-job.yaml`):** Converted into a Kubernetes `Job`. A Job runs a task exactly once until it succeeds, and then terminates permanently, freeing up CPU and RAM. It loops internally using `curl` to wait for Elasticsearch and Kibana to pass their readiness probes before importing the dashboards.

## 6. Resource Limits & Memory Management (OOMKilled)
*   **Docker Compose:** Containers generally consumed as much memory as the host allowed.
*   **Kubernetes:** We implemented strict resource `limits` and `requests`. 
    *   **The Problem:** Elasticsearch crashed with `OOMKilled` (Exit Code 137) because it tried to allocate a 2GB Java Heap inside a container strictly limited to 2GB, leaving 0 RAM for the OS overhead.
    *   **The Fix:** Adjusted the `ES_JAVA_OPTS` to `-Xms1g -Xmx1g` (1GB heap) in `03-elasticsearch.yaml`, perfectly balancing it within the 2GB container limit for WSL environments.

## 7. Connecting to External Services (The Kafka Pivot)
*   **Docker Compose:** Connecting the Engine to an external Kafka server (`192.168.10.57`) just worked because Docker Desktop NAT allows raw local network access.
*   **Kubernetes:** The Engine can still connect natively to external servers! By modifying the `bootstrap_servers` in the `02-configmaps.yaml` from `kafka.tlsoc.svc.cluster.local:9092` to `192.168.10.57:9094`, the cluster seamlessly routes traffic outside the cluster. 
    *   *Note:* The Engine's offset behavior was also updated from `latest` to `earliest` in the cluster to ensure it processes historical logs sitting on external brokers upon connecting.
