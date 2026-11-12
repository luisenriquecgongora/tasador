# syntax=docker/dockerfile:1.4
# Tasador engine. Build arg EXTRAS picks the optional local stages:
#   docker build --build-arg EXTRAS=ocr,barcode,heic .   (default: everything, ~450 MB RAM with OCR)
#   docker build --build-arg EXTRAS=barcode .            (small hosts, e.g. a 512 MB instance)
FROM python:3.12-slim AS builder
ARG EXTRAS=ocr,barcode,heic
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[${EXTRAS}]"

FROM python:3.12-slim AS runner
# OpenCV (pulled in by the OCR extra) needs these two shared libraries at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -r tasador && useradd -r -g tasador -d /app -s /sbin/nologin tasador
COPY --from=builder /install /usr/local
WORKDIR /app
USER tasador
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1
CMD ["sh", "-c", "uvicorn tasador_engine.main:app_from_env --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
