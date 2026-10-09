FROM python:3.13-alpine
WORKDIR /app
COPY decypharr_fallback.py .
EXPOSE 8283
USER nobody
CMD ["python", "-u", "decypharr_fallback.py"]
