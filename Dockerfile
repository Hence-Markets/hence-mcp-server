# Hence MCP server. Standard library only: no dependencies to install or audit.
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY gateway/ ./gateway/
COPY data/ ./data/
RUN useradd --uid 10002 --create-home gateway
USER gateway
EXPOSE 4340
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4340/healthz', timeout=4)"]
CMD ["python", "-m", "gateway.app"]
