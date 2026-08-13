# ---------------------------------------------------------------- frontend
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then \
      npm ci --no-audit --no-fund; \
    else \
      echo "no package-lock.json - falling back to npm install (run 'npm install' in frontend/ and commit the lockfile for reproducible builds)"; \
      npm install --no-audit --no-fund; \
    fi
COPY frontend/ ./
RUN npm run build \
    && test -f dist/index.html \
    && test -d dist/assets \
    && echo "frontend bundle OK: $(ls dist/assets | wc -l) assets"

# ---------------------------------------------------------------- python deps
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    STATIC_DIR=/srv/static

RUN useradd --create-home --uid 10001 app
WORKDIR /srv

COPY --from=builder /opt/venv /opt/venv
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/config ./config
COPY --from=web /web/dist ./static

RUN test -f /srv/static/index.html || (echo "FATAL: frontend missing from image" && exit 1)

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
