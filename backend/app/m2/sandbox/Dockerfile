# Build and run environment for generated apps (stack contract: Node 22, Python 3.11).
FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
