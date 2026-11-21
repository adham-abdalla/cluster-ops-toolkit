# fleetcheck agent image: small, non-root, read-only friendly.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir pyyaml==6.0.2
WORKDIR /app
COPY fleetcheck/ fleetcheck/
COPY examples/ examples/
COPY examples/config.yml /etc/fleetcheck/config.yml
USER 65534:65534
EXPOSE 9105
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9105/healthz', timeout=2)"
ENTRYPOINT ["python", "-m", "fleetcheck"]
CMD ["serve", "--config", "/etc/fleetcheck/config.yml"]
