#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=========================================================="
echo "🚀 [POC 01] Desplegando en Namespaces Segmentados:"
echo "   - Cargas de trabajo: workers"
echo "   - Servidor MCP:      mcps"
echo "   - Microagente SRE:   agents"
echo "=========================================================="

# 0. Asegurar creación de los 3 namespaces
echo "📁 0. Creando namespaces 'workers', 'mcps', 'agents'..."
kubectl create namespace workers --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace mcps --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace agents --dry-run=client -o yaml | kubectl apply -f -

# 1. Desplegar Workload que falla en namespace 'workers'
echo "📦 1. Desplegando Pod roto en namespace 'workers'..."
kubectl apply -f "$BASE_DIR/broken-workload/broken-pod.yaml"

# 2. Desplegar Servidor MCP en namespace 'mcps'
echo "🛠️ 2. Desplegando Servidor MCP en namespace 'mcps'..."
kubectl apply -f "$BASE_DIR/k8s-mcp-server/k8s/rbac.yaml"
kubectl apply -f "$BASE_DIR/k8s-mcp-server/k8s/deployment.yaml"

# 3. Desplegar Microagente SRE en namespace 'agents'
echo "🤖 3. Desplegando Microagente SRE en namespace 'agents'..."
kubectl apply -f "$BASE_DIR/sre-microagent/k8s/rbac.yaml"

# Crear secreto en namespace 'agents' si se provee la variable o existe el .env
if [ -z "$GOOGLE_API_KEY" ] && [ -f "$BASE_DIR/../../langchain-langgraph/01-simple-agent/.env" ]; then
    API_KEY_VAL=$(grep -E '^GOOGLE_API_KEY=' "$BASE_DIR/../../langchain-langgraph/01-simple-agent/.env" | cut -d '=' -f2-)
    if [ -n "$API_KEY_VAL" ]; then
        GOOGLE_API_KEY="$API_KEY_VAL"
    fi
fi

if [ -n "$GOOGLE_API_KEY" ]; then
    echo "🔑 Configurando secreto en namespace 'agents' con GOOGLE_API_KEY..."
    kubectl create secret generic sre-agent-secrets -n agents \
      --from-literal=GOOGLE_API_KEY="$GOOGLE_API_KEY" \
      --dry-run=client -o yaml | kubectl apply -f -
else
    echo "⚠️ Creando secreto base en namespace 'agents' (recuerda actualizar tu API key)..."
    kubectl apply -f "$BASE_DIR/sre-microagent/k8s/secret.yaml"
fi

kubectl apply -f "$BASE_DIR/sre-microagent/k8s/deployment.yaml"

echo ""
echo "=========================================================="
echo "✅ Todos los recursos fueron desplegados exitosamente!"
echo "=========================================================="
echo ""
echo "Estado de los namespaces:"
kubectl get pods -n workers -l app=payment-service
kubectl get pods -n mcps -l app=k8s-mcp-server
kubectl get pods -n agents -l app=sre-microagent
echo ""
echo "Para seguir los logs del agente SRE:"
echo "kubectl logs -f -n agents -l app=sre-microagent"
