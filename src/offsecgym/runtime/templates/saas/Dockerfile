FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY saas_service.py gateway_idle.py http_worker.py fixture.json /app/
USER 10001:10001
CMD ["python", "-u", "/app/saas_service.py"]
