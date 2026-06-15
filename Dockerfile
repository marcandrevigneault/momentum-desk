# Single deployable image: builds the dashboard, then serves it + the API from
# one uvicorn process.

# --- stage 1: build the React dashboard ---
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- stage 2: python backend serving the built dashboard ---
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY momentum_desk ./momentum_desk
# editable install keeps the package at /app so server.py can locate web/dist
RUN pip install --no-cache-dir -e .
COPY --from=web /web/dist ./web/dist

EXPOSE 8000
# paper/mock defaults bake in; mount a config.yaml to point at a real feed
CMD ["uvicorn", "momentum_desk.server:app", "--host", "0.0.0.0", "--port", "8000"]
