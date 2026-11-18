#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "🚀 [Ejecución Local Híbrida] Probando POC 01 contra tu clúster actual de kubectl..."

# 1. Aplicar workload roto en el clúster
echo "1. Desplegando Pod roto en K8s..."
kubectl apply -f "$BASE_DIR/broken-workload/broken-pod.yaml"

# 2. Esperar a que el Pod entre en CrashLoopBackOff o Error
echo "2. Esperando a que el Pod entre en fallo..."
sleep 4
kubectl get pods -l app=payment-service

# 3. Ejecutar el Agente SRE directamente en modo oneshot usando kubeconfig local
echo "3. Ejecutando Microagente SRE en modo oneshot..."
export TARGET_NAMESPACE=default
export RUN_MODE=oneshot
# Usar el .env existente de 01-simple-agent si no está definido GOOGLE_API_KEY
if [ -z "$GOOGLE_API_KEY" ] && [ -f "$BASE_DIR/../../langchain-langgraph/01-simple-agent/.env" ]; then
    export $(grep -v '^#' "$BASE_DIR/../../langchain-langgraph/01-simple-agent/.env" | xargs)
fi

python3 "$BASE_DIR/sre-microagent/agent.py"

