FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl procps && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /opt/hermes && \
    cd /opt/hermes && uv pip install --system -e ".[all]"

COPY bridge_server/requirements.txt /app/bridge_server/requirements.txt
RUN pip install --no-cache-dir -r /app/bridge_server/requirements.txt

COPY bridge_server/ /app/bridge_server/
COPY web/ /app/web/
COPY skills/ /app/skills/
COPY docker-entrypoint.sh /app/

RUN chmod +x /app/docker-entrypoint.sh && \
    mkdir -p /app/data

WORKDIR /app
EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
