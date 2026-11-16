import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import MessagesState, StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# Cargar variables de entorno desde .env
load_dotenv()

# 1. Definir herramientas (Tools)
def multiply(a: int, b: int) -> int:
    """Multiply a and b.

    Args:
        a: first int
        b: second int
    """
    return a * b

def add(a: int, b: int) -> int:
    """Adds a and b.

    Args:
        a: first int
        b: second int
    """
    return a + b

def divide(a: int, b: int) -> float:
    """Divide a and b.

    Args:
        a: first int
        b: second int
    """
    return a / b

tools = [add, multiply, divide]

# 2. Modelo (Gemini con soporte de herramientas)
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
llm_with_tools = llm.bind_tools(tools)

# 3. Mensaje del Sistema y Nodo Asistente
sys_msg = SystemMessage(content="You are a helpful assistant tasked with performing arithmetic on a set of inputs.")

def assistant(state: MessagesState):
    return {"messages": [llm_with_tools.invoke([sys_msg] + state["messages"])]}

# 4. Construcción del Grafo
builder = StateGraph(MessagesState)

builder.add_node("assistant", assistant)
builder.add_node("tools", ToolNode(tools))

builder.add_edge(START, "assistant")
builder.add_conditional_edges(
    "assistant",
    tools_condition,
)
builder.add_edge("tools", "assistant")

# 5. Compilar con Memoria (Checkpointer)
memory = MemorySaver()
react_graph = builder.compile(checkpointer=memory)

# 6. Prueba con pretty_print() y memoria multi-turno
if __name__ == "__main__":
    # Guardar como imagen PNG
    try:
        png_data = react_graph.get_graph(xray=True).draw_mermaid_png()
        with open("graph.png", "wb") as f:
            f.write(png_data)
        print("📊 Grafo exportado como 'graph.png'\n")
    except Exception as e:
        pass

    # Para que la memoria funcione entre llamadas, LangGraph usa un thread_id
    config = {"configurable": {"thread_id": "conversacion_1"}}

    # Primer turno:
    print("=" * 50)
    print("🔹 Turno 1: Add 3 and 4.")
    print("=" * 50)
    messages = [HumanMessage(content="Add 3 and 4.")]
    result = react_graph.invoke({"messages": messages}, config=config)
    for m in result['messages']:
        m.pretty_print()

    # Segundo turno (probando que recuerda el turno anterior):
    print("\n" + "=" * 50)
    print("🔹 Turno 2: Multiply that by 2. (Recordando el resultado anterior)")
    print("=" * 50)
    messages = [HumanMessage(content="Multiply that by 2.")]
    result = react_graph.invoke({"messages": messages}, config=config)
    for m in result['messages']:
        m.pretty_print()
