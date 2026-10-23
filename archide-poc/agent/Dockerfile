FROM python:3.11-slim

WORKDIR /app

COPY __init__.py /app/agent/__init__.py
COPY core.py /app/agent/core.py
COPY sandbox.py /app/agent/sandbox.py

ENTRYPOINT ["python", "-m", "agent.sandbox"]
