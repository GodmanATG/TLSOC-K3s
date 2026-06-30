#!/bin/bash

# A powerful SOC debugging script that searches across ALL FOSS-Engine volumes.
# Features:
# 1. Supports advanced regex for searching multiple logs at once.
# 2. Automatically spins up temporary debug pods to search volumes of scaled-down (sleeping) engines.

PATTERN=$1

if [ -z "$PATTERN" ]; then
    echo "Usage: ./search_logs.sh '<regex_pattern>'"
    echo "Example (Single search): ./search_logs.sh '192.168.1.100'"
    echo "Example (Multiple search): ./search_logs.sh 'postfix|failed password|root'"
    exit 1
fi

echo "🔎 Searching for pattern '$PATTERN' across ALL volumes (active and sleeping)..."
echo "--------------------------------------------------------"

# Get all PVCs belonging to the StatefulSet
PVCS=$(kubectl get pvc -n tlsoc -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^parser-output-foss-engine-')

if [ -z "$PVCS" ]; then
    echo "No parser-output PVCs found."
    exit 1
fi

for PVC in $PVCS; do
    # Extract the pod name (e.g., from parser-output-foss-engine-0 -> foss-engine-0)
    POD_NAME=${PVC#parser-output-}
    
    # Check if the StatefulSet pod is currently running
    POD_STATE=$(kubectl get pod $POD_NAME -n tlsoc -o jsonpath='{.status.phase}' 2>/dev/null)
    
    if [ "$POD_STATE" == "Running" ]; then
        echo "🟢 [$POD_NAME] is ACTIVE. Searching live container..."
        kubectl exec -n tlsoc $POD_NAME -c foss-engine -- sh -c "grep -E -i --color=always -H '$PATTERN' /var/log/soc_output/*.json 2>/dev/null"
    else
        echo "💤 [$POD_NAME] is SCALED DOWN. Spinning up temporary debug pod to read its sleeping volume..."
        DEBUG_POD="debug-$POD_NAME"
        
        # Deploy a tiny temporary busybox pod to mount the sleeping Longhorn volume
        cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: $DEBUG_POD
  namespace: tlsoc
spec:
  containers:
  - name: debugger
    image: busybox
    command: ["sleep", "3600"]
    volumeMounts:
    - name: data
      mountPath: /data
  volumes:
  - name: data
    persistentVolumeClaim:
      claimName: $PVC
  restartPolicy: Never
EOF
        
        # Wait up to 60 seconds for Longhorn to attach the volume to the temp pod
        kubectl wait --for=condition=Ready pod/$DEBUG_POD -n tlsoc --timeout=60s >/dev/null 2>&1
        
        if [ $? -eq 0 ]; then
            # Execute the regex search inside the temp pod
            kubectl exec -n tlsoc $DEBUG_POD -- sh -c "grep -E -i --color=always -H '$PATTERN' /data/*.json 2>/dev/null"
        else
            echo "   ⚠️ Failed to mount volume. It might still be locked by a recently disconnected node."
        fi
        
        # Destroy the temporary pod to free up the volume
        echo "   Cleaning up temporary pod gracefully (this takes a few seconds)..."
        kubectl delete pod $DEBUG_POD -n tlsoc >/dev/null 2>&1
    fi
    echo "--------------------------------------------------------"
done

echo "✅ Search complete."
