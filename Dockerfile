# Single container for momentum-desk. Runs three processes under supervisord:
#   - ib_proxy  (Python HTTP->HTTPS re-proxy; evades IBKR's JA3/JA4 WAF block)
#   - ibeam     (headless Chromium auto-submits IBKR login + runs the CP gateway)
#   - dashboard (uvicorn :8000, serves the API + the built Vite SPA)
#
# The IBKR Client Portal Gateway (localhost:5000) is baked into the image so a
# cloud boot is zero-click except the one-time phone 2FA. ibeam drives it
# headlessly; only approving the IBKR Mobile push on your phone is manual.
#
# Pinned to bookworm: it still ships openjdk-17 (the gateway runtime) and the
# chromium/chromium-driver ibeam's Selenium stack uses.

# ---------- Stage 1: build the React dashboard ----------
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---------- Stage 2: ibeam source (Python files + requirements only) ----------
FROM voyz/ibeam:latest AS ibeam-src

# ---------- Stage 3: python runtime ----------
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# OS deps:
#   openjdk-17-jre-headless        -> IBKR CP gateway runtime
#   chromium, chromium-driver      -> ibeam's Selenium auto-login
#   xvfb, dbus-x11, xfonts-*       -> ibeam drives Chrome inside an Xvfb display
#   lib*                           -> Chromium runtime libs
#   supervisor, tini               -> process management
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip procps supervisor tini \
      openjdk-17-jre-headless \
      chromium chromium-driver \
      xvfb dbus-x11 xfonts-base xfonts-75dpi xfonts-100dpi \
      libnss3 libatk-bridge2.0-0 libatk1.0-0 libcups2 libxkbcommon0 \
      libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2 \
      libpango-1.0-0 libcairo2 fonts-liberation libjpeg62-turbo libwebp7 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python venv shared by the app, ibeam and ib_proxy, so one interpreter drives all.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Dependency layer (cached unless pyproject changes). Editable install keeps the
# package at /app so server.py can locate ../web/dist at /app/web/dist.
COPY pyproject.toml ./
COPY momentum_desk ./momentum_desk
RUN pip install -e .

# ibeam: copy its scripts + install its Python deps into the same venv. Env vars
# (IBEAM_*) come from Fly secrets at runtime (mapped in supervisord.conf).
# requirements.txt lives at /srv/requirements.txt in the voyz image; PYTHONPATH
# needs /srv so ibeam_starter.py's `from ibeam import ...` resolves.
COPY --from=ibeam-src /srv/requirements.txt /tmp/ibeam-requirements.txt
COPY --from=ibeam-src /srv/ibeam /srv/ibeam
RUN pip install -r /tmp/ibeam-requirements.txt
ENV PYTHONPATH="/srv:/srv/ibeam"

# Project source + built SPA.
COPY . .
COPY --from=web /web/dist /app/web/dist

# IBKR Client Portal Gateway: download + bake in. Post-unzip, point its upstream
# at our Python re-proxy (127.0.0.1:5002) instead of the JVM's default TLS stack,
# which IBKR's WAF JA3/JA4-fingerprints and 403s. See momentum_desk/ib_proxy.py.
RUN mkdir -p /opt/gateway && \
    curl -LsSf https://download2.interactivebrokers.com/portal/clientportal.gw.zip -o /tmp/gw.zip && \
    unzip -q /tmp/gw.zip -d /opt/gateway && \
    chmod +x /opt/gateway/bin/run.sh && \
    sed -i 's|proxyRemoteSsl: true|proxyRemoteSsl: false|' /opt/gateway/root/conf.yaml && \
    sed -i 's|proxyRemoteHost: "https://api.ibkr.com"|proxyRemoteHost: "http://127.0.0.1:5002"|' /opt/gateway/root/conf.yaml && \
    rm /tmp/gw.zip

# Runtime dirs (the Fly volume mounts at /app/data).
RUN mkdir -p /app/data /app/logs

# Self-contained config (overwrites the apt default), so CMD points straight at it.
COPY deploy/supervisord.conf /etc/supervisor/supervisord.conf

# Dashboard port (public). Gateway 127.0.0.1:5000 + proxy 127.0.0.1:5002 internal.
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf", "-n"]
