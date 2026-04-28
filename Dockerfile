FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml ./
RUN pip install --prefix=/install \
    "fastapi==0.115.6" \
    "uvicorn[standard]==0.32.1" \
    "httpx==0.28.1" \
    "curl_cffi==0.7.4" \
    "jinja2==3.1.4" \
    "python-multipart==0.0.20" \
    "itsdangerous==2.2.0" \
    "bcrypt==4.2.1" \
    "PyYAML==6.0.2" \
    "pydantic==2.10.4" \
    "APScheduler==3.11.0"


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACTIVATETRACKER_CONFIG=/app/config.yaml \
    ACTIVATETRACKER_DB=/data/tracker.db

RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --home /app --shell /sbin/nologin app \
 && mkdir -p /app /data \
 && chown -R app:app /app /data

COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=app:app app /app/app

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
