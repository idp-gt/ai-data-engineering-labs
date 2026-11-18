import os
import sys
import time
import asyncio
from typing import TypedDict, List, Optional, Literal
from dotenv import load_dotenv
from kubernetes import client, config
from mcp import ClientSession
from mcp.client.sse import sse_client
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

# Cargar variables de entorno
load_dotenv()

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://k8s-mcp-server:8000/sse")
TARGET_NAMESPACE = os.environ.get("TARGET_NAMESPACE", "default")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))
RUN_MODE = os.environ.get("RUN_MODE", "oneshot")  # 'oneshot' o 'daemon'

def get_k8s_client() -> client.CoreV1Api:
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client.CoreV1Api()

def get_llm():
    """Inicializa el LLM según el proveedor disponible (Gemini, OpenAI, Ollama)."""
    if os.environ.get("GOOGLE_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        return ChatGoogleGenerativeAI(model=model_name, temperature=0)
    elif os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return ChatOpenAI(model=model_name, temperature=0)
    else:
        # Fallback a Ollama local
        from langchain_community.chat_models import ChatOllama
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(base_url=ollama_url, model="llama3", temperature=0)

async def call_mcp_tool_remote(tool_name: str, arguments: dict) -> str:
    """Invoca una herramienta en el servidor MCP vía protocolo SSE."""
    print(f"  [MCP Client] Conectando a {MCP_SERVER_URL} para invocar '{tool_name}'...")
    try:
        async with sse_client(MCP_SERVER_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                texts = [c.text for c in result.content if hasattr(c, "text")]
                return "\n".join(texts)
    except Exception as e:
        print(f"  [MCP Client WARNING] Error al conectar con servidor MCP: {e}. Usando fallback local...")
        return fallback_local_k8s_call(tool_name, arguments)

def fallback_local_k8s_call(tool_name: str, arguments: dict) -> str:
    """Fallback directo a la API de Kubernetes en caso de prueba local sin servidor MCP."""
    v1 = get_k8s_client()
    pod_name = arguments.get("pod_name")
    namespace = arguments.get("namespace", "default")
    if tool_name == "get_pod_logs":
        try:
            return v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, previous=True, tail_lines=50)
        except Exception:
            try:
                return v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=50)
            except Exception as e:
                return f"Error en logs: {e}"
    elif tool_name == "describe_pod":
        try:
            pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            return f"Pod: {pod.metadata.name}, Phase: {pod.status.phase}, Statuses: {pod.status.container_statuses}"
        except Exception as e:
            return f"Error describe: {e}"
    return "Tool no reconocida"

# -------------------------------------------------------------
# 1. Definición del Estado de LangGraph
# -------------------------------------------------------------
class SREState(TypedDict):
    namespace: str
    failed_pods: List[dict]
    current_pod: Optional[dict]
    pod_logs: str
    pod_description: str
    analysis: str
    verdict: str

# -------------------------------------------------------------
# 2. Nodos del Grafo
# -------------------------------------------------------------
def scan_failed_pods(state: SREState) -> dict:
    """Escanea el namespace de K8s buscando Pods en CrashLoopBackOff o Error."""
    v1 = get_k8s_client()
    ns = state.get("namespace", "default")
    print(f"\n🔍 [SRE Node: scan_failed_pods] Inspeccionando pods en namespace '{ns}'...")
    
    pod_list = v1.list_namespaced_pod(namespace=ns)
    failed = []

    for pod in pod_list.items:
        pod_name = pod.metadata.name
        is_failing = False
        reason = "Unknown"

        if pod.status.phase in ["Failed", "CrashLoopBackOff"]:
            is_failing = True
            reason = pod.status.phase

        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "Error", "RunContainerError"]:
                    is_failing = True
                    reason = cs.state.waiting.reason
                    break
                elif cs.state.terminated and cs.state.terminated.exit_code != 0:
                    is_failing = True
                    reason = f"Terminated (ExitCode: {cs.state.terminated.exit_code})"
                    break

        if is_failing:
            print(f"  ⚠️ Pod fallido detectado: {pod_name} (Razón: {reason})")
            failed.append({"name": pod_name, "namespace": ns, "reason": reason})

    if not failed:
        print("  ✅ No se encontraron Pods con fallos en este escaneo.")

    current = failed[0] if failed else None
    return {"failed_pods": failed, "current_pod": current}

def fetch_mcp_context(state: SREState) -> dict:
    """Extrae logs y descripción llamando a las herramientas del Servidor MCP."""
    curr = state.get("current_pod")
    if not curr:
        return {"pod_logs": "", "pod_description": ""}

    pod_name = curr["name"]
    ns = curr["namespace"]
    print(f"\n🛠️ [SRE Node: fetch_mcp_context] Solicitando diagnóstico al Servidor MCP para '{pod_name}'...")

    # Ejecutar llamadas MCP de manera asíncrona
    async def _fetch():
        logs = await call_mcp_tool_remote("get_pod_logs", {"pod_name": pod_name, "namespace": ns})
        desc = await call_mcp_tool_remote("describe_pod", {"pod_name": pod_name, "namespace": ns})
        return logs, desc

    logs, desc = asyncio.run(_fetch())
    print(f"  📥 Logs obtenidos ({len(logs)} caracteres)")
    print(f"  📥 Descripción de Pod obtenida ({len(desc)} caracteres)")
    return {"pod_logs": logs, "pod_description": desc}

def analyze_root_cause(state: SREState) -> dict:
    """Analiza los logs y descripción usando el LLM para hallar la causa raíz."""
    curr = state.get("current_pod")
    if not curr:
        return {"analysis": "Sin pod para analizar", "verdict": "Sin fallo detectado"}

    pod_name = curr["name"]
    print(f"\n🧠 [SRE Node: analyze_root_cause] Consultando al LLM sobre la causa raíz de '{pod_name}'...")

    llm = get_llm()
    system_prompt = (
        "Eres un Senior Site Reliability Engineer (SRE) especializado en diagnosticar incidentes en Kubernetes.\n"
        "Se te proporcionarán los logs y la descripción (eventos y estado) de un pod en CrashLoopBackOff o Error.\n"
        "Debes analizar la información y determinar con exactitud la causa raíz técnica.\n"
        "REGLA OBLIGATORIA: Al final de tu respuesta, en la última línea exacta, debes incluir un veredicto en este formato:\n"
        "VEREDICTO: El Pod <nombre_del_pod> falló porque le falta la variable <NOMBRE_VARIABLE>."
    )

    user_prompt = f"""
    Pod Name: {pod_name}
    Namespace: {curr['namespace']}
    Razón del fallo: {curr['reason']}

    === LOGS DEL POD ===
    {state.get('pod_logs', '')}

    === DESCRIPCIÓN DEL POD Y EVENTOS ===
    {state.get('pod_description', '')}

    Por favor:
    1. Explica brevemente qué causó el fallo.
    2. Proporciona la línea exacta del VEREDICTO final.
    """

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
    ])

    analysis_text = response.content if isinstance(response.content, str) else str(response.content)

    # Extraer línea de verificación
    verdict_line = ""
    for line in analysis_text.splitlines():
        if "falló porque" in line:
            verdict_line = line.replace("VEREDICTO:", "").strip()
            break

    if not verdict_line:
        verdict_line = f"El Pod {pod_name} falló debido a problemas en su configuración."

    return {"analysis": analysis_text, "verdict": verdict_line}

def report_verification(state: SREState) -> dict:
    """Imprime el veredicto final verificado según el requerimiento de la POC."""
    verdict = state.get("verdict", "")
    curr = state.get("current_pod", {})
    pod_name = curr.get("name", "Desconocido")

    print("\n" + "=" * 70)
    print("📢 [SRE AGENT - VERIFICACIÓN]")
    print(f"👉 {verdict}")
    print("=" * 70 + "\n")
    return {}

# -------------------------------------------------------------
# 3. Flujo Condicional y Construcción del Grafo
# -------------------------------------------------------------
def should_diagnose(state: SREState) -> Literal["fetch_mcp_context", "__end__"]:
    if state.get("current_pod"):
        return "fetch_mcp_context"
    return END

workflow = StateGraph(SREState)
workflow.add_node("scan_failed_pods", scan_failed_pods)
workflow.add_node("fetch_mcp_context", fetch_mcp_context)
workflow.add_node("analyze_root_cause", analyze_root_cause)
workflow.add_node("report_verification", report_verification)

workflow.add_edge(START, "scan_failed_pods")
workflow.add_conditional_edges("scan_failed_pods", should_diagnose)
workflow.add_edge("fetch_mcp_context", "analyze_root_cause")
workflow.add_edge("analyze_root_cause", "report_verification")
workflow.add_edge("report_verification", END)

sre_agent_graph = workflow.compile()

# -------------------------------------------------------------
# 4. Loop de Ejecución (Oneshot o Daemon)
# -------------------------------------------------------------
def run_cycle():
    initial_state = {
        "namespace": TARGET_NAMESPACE,
        "failed_pods": [],
        "current_pod": None,
        "pod_logs": "",
        "pod_description": "",
        "analysis": "",
        "verdict": ""
    }
    sre_agent_graph.invoke(initial_state)

if __name__ == "__main__":
    print("🚀 [SRE Microagent] Iniciando agente de diagnóstico LangGraph + MCP...")
    print(f"   Modo: {RUN_MODE} | Namespace objetivo: {TARGET_NAMESPACE}")

    if RUN_MODE == "daemon":
        while True:
            try:
                run_cycle()
            except Exception as e:
                print(f"❌ Error en ciclo de análisis: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)
    else:
        run_cycle()

