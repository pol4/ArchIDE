# ArchIDE — all-in-one image for a local workstation.
#
# One container with everything inside: the HTTP server, the UI and the built-in
# agent. The agent runs *in process* (`runner=local`), so the container needs no
# Docker socket and no extra services — start it and open the browser.
#
# The very same image is what the Kubernetes deployment runs (see
# ../helm/archide): there is one source tree and one build, only the surrounding
# parameters differ.
#
# Build (from the repository root or via deploy/build.sh):
#   docker build -f deploy/Dockerfile -t archide:latest .
FROM python:3.11-slim

# No third-party Python packages: the server and the agent use the stdlib only.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ARCHIDE_DATA=/data \
    ARCHIDE_HOST=0.0.0.0 \
    ARCHIDE_PORT=8000 \
    ARCHIDE_RUNNER=local

WORKDIR /app

# the single source tree (server + backend + agent + frontend)
COPY archide-poc/server.py /app/
COPY archide-poc/backend /app/backend
COPY archide-poc/agent /app/agent
COPY archide-poc/frontend /app/frontend
COPY archide-poc/agents /app/agents
COPY archide-poc/requirements.txt /app/

# the seed agent manifest (the rest of /data is runtime state on a volume)
COPY archide-poc/data/agents.json /app/data/agents.json

COPY deploy/entrypoint.sh /usr/local/bin/archide-entrypoint
RUN chmod +x /usr/local/bin/archide-entrypoint \
    && mkdir -p /data \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin archide \
    && chown -R 1000:1000 /app /data

# Non-root from the start: the Kubernetes cluster enforces the restricted
# Pod Security Standard, and a workstation gains nothing from running as root.
USER 1000:1000

# The data directory (models, runs, config, layouts) is the only writable path.
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/agents', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/archide-entrypoint"]
