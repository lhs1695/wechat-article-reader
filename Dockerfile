FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_EXTRA_INDEX_URL=
ENV WECHAT_ARTICLE_READER_RUNTIME_DIR=/app/.runtime
ENV WECHAT_ARTICLE_READER_EXPORT__DEFAULT_OUTPUT_DIR=/app/output

COPY pyproject.toml README.md ./
COPY requirements/docker.lock.txt requirements/docker.lock.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 --timeout 60 --require-hashes -r requirements/docker.lock.txt

COPY src/ src/

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 --timeout 60 --no-deps . \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.runtime /app/output \
    && chown -R appuser:appuser /app/.runtime /app/output

EXPOSE 8000

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=3)"

CMD ["python", "-c", "from wechat_article_reader.presentation.web import run; run(host='0.0.0.0')"]
