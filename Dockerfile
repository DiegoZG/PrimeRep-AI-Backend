FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --system --uid 10001 --create-home appuser
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY app /app/app
COPY scripts/run_coach_worker.py scripts/run_email_worker.py scripts/release_preflight.py /app/scripts/

EXPOSE 8000
USER appuser
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
