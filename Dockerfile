FROM python:3.12-slim AS dependencies
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-mcp.txt requirements-remote.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements-remote.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
RUN useradd --uid 10001 --create-home mcp \
    && mkdir /data && chown mcp:mcp /data
COPY lynse_mcp.py lynse_auth.py lynse_remote.py lynse_account.py lynse_oauth_store.py ./
COPY lynse_ui.py ./
COPY web/dist/lynse-app.html ./web/dist/lynse-app.html
USER mcp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "lynse_remote.py", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test-ui
COPY lynse_test_client.py ./

FROM runtime AS production
