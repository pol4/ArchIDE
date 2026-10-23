# Kilo Code CLI sandbox image.
#
# Installs the Kilo Code CLI and runs it against the ArchIDE LLM gateway.
# Override the package if your CLI is published under a different name:
#   docker build --build-arg KILO_PKG=@kilocode/cli -t archide-kilo:latest agents/kilo
FROM node:20-slim

ARG KILO_PKG=@kilocode/cli
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "${KILO_PKG}" \
    && npm cache clean --force

WORKDIR /app
COPY entrypoint.js /app/entrypoint.js

ENTRYPOINT ["node", "/app/entrypoint.js"]
