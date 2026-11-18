import os
import sys
import json
from typing import Optional
from kubernetes import client, config
from mcp.server.fastmcp import FastMCP

# Inicializar servidor FastMCP
mcp = FastMCP(
    "k8s-mcp-server",
    instructions="Servidor MCP para inspección y diagnóstico SRE de Kubernetes",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000"))
)

def get_k8s_client() -> client.CoreV1Api:
    """Carga configuración dentro del clúster o archivo kubeconfig local."""
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client.CoreV1Api()

@mcp.tool()
def get_pod_logs(pod_name: str, namespace: str = "default", tail_lines: int = 100) -> str:
    """Extrae los logs de un Pod en Kubernetes.
    
    Args:
        pod_name: Nombre exacto del pod.
        namespace: Namespace donde reside el pod (por defecto 'default').
        tail_lines: Cantidad de líneas recientes a leer.
    """
    v1 = get_k8s_client()
    logs = ""
    # Intentar leer logs del contenedor actual
    try:
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines
        )
    except Exception as e:
        logs = f"Aviso al leer logs actuales: {str(e)}\n"

    # Si está en CrashLoopBackOff, los logs del contenedor previo guardan la causa del crash
    if not logs or "Aviso" in logs:
        try:
            prev_logs = v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                previous=True,
                tail_lines=tail_lines
            )
            logs += f"\n--- Logs del contenedor anterior (Crash anterior) ---\n{prev_logs}"
        except Exception:
            pass

    return logs.strip() if logs.strip() else f"No se encontraron logs para el pod {pod_name}."

@mcp.tool()
def describe_pod(pod_name: str, namespace: str = "default") -> str:
    """Describe el estado detallado de un Pod y sus eventos recientes (equivalente a kubectl describe pod).
    
    Args:
        pod_name: Nombre del pod.
        namespace: Namespace del pod.
    """
    v1 = get_k8s_client()
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as e:
        return f"Error al obtener pod {pod_name} en namespace {namespace}: {str(e)}"

    # Extraer información relevante de estado
    status_summary = {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "node_name": pod.spec.node_name,
        "restart_policy": pod.spec.restart_policy,
        "containers": []
    }

    # Inspeccionar estado de los contenedores
    if pod.status.container_statuses:
        for cs in pod.status.container_statuses:
            c_info = {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "image": cs.image
            }
            if cs.state.waiting:
                c_info["state"] = "Waiting"
                c_info["reason"] = cs.state.waiting.reason
                c_info["message"] = cs.state.waiting.message
            elif cs.state.terminated:
                c_info["state"] = "Terminated"
                c_info["reason"] = cs.state.terminated.reason
                c_info["exit_code"] = cs.state.terminated.exit_code
                c_info["message"] = cs.state.terminated.message
            elif cs.state.running:
                c_info["state"] = "Running"
                c_info["started_at"] = str(cs.state.running.started_at)
            status_summary["containers"].append(c_info)

    # Extraer eventos recientes relacionados con el Pod
    events_summary = []
    try:
        events = v1.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}"
        )
        for ev in events.items:
            events_summary.append({
                "type": ev.type,
                "reason": ev.reason,
                "message": ev.message,
                "count": ev.count
            })
    except Exception:
        pass

    output = {
        "pod_status": status_summary,
        "recent_events": events_summary
    }

    return json.dumps(output, indent=2)

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "sse")
    print(f"[k8s-mcp-server] Iniciando servidor con transporte: {transport}...")
    if transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")

