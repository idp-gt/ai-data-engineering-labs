#!/usr/bin/env bash
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "🧹 Eliminando recursos de los namespaces workers, mcps y agents..."
kubectl delete -f "$BASE_DIR/broken-workload/broken-pod.yaml" --ignore-not-found
kubectl delete -f "$BASE_DIR/sre-microagent/k8s/deployment.yaml" --ignore-not-found
kubectl delete -f "$BASE_DIR/sre-microagent/k8s/rbac.yaml" --ignore-not-found
kubectl delete secret sre-agent-secrets -n agents --ignore-not-found
kubectl delete -f "$BASE_DIR/k8s-mcp-server/k8s/deployment.yaml" --ignore-not-found
kubectl delete -f "$BASE_DIR/k8s-mcp-server/k8s/rbac.yaml" --ignore-not-found

echo "🧹 Eliminando namespaces..."
kubectl delete namespace workers mcps agents --ignore-not-found
echo "✅ Limpieza completada."
