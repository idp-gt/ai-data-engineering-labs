FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    RUN_MODE=daemon \
    TARGET_NAMESPACE=default \
    MCP_SERVER_URL=http://k8s-mcp-server:8000/sse

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

CMD ["python", "agent.py"]

