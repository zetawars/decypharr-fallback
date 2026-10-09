FROM python:3.13-alpine
WORKDIR /app
COPY decypharr_fallback.py .
EXPOSE 8283
USER nobody
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD wget -qO- "http://127.0.0.1:${PORT:-8283}/health" >/dev/null || exit 1
CMD ["python", "-u", "decypharr_fallback.py"]
