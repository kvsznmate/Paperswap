# Python 3.12 slim image
FROM python:3.12-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg2-binary ships its own libs, so no build deps are required.

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port
EXPOSE 8000

# Load shedding (#58). The uvicorn CLI reads any UVICORN_<OPTION> variable as
# that flag, so docker-compose.yml can override these without a rebuild.
#   LIMIT_CONCURRENCY  open connections OR in-flight requests above which
#                      uvicorn answers 503 before the app sees the request.
#                      Idle keep-alive connections count, hence the short
#                      keep-alive below. See docs/RATE_LIMITS.md.
#   TIMEOUT_KEEP_ALIVE seconds an idle connection is held open.
ENV UVICORN_LIMIT_CONCURRENCY=64
ENV UVICORN_TIMEOUT_KEEP_ALIVE=3

# Run FastAPI app with Uvicorn. Exec form, so uvicorn is PID 1 and receives
# SIGTERM directly: the lifespan shutdown flushes the buffered request logs.
# --no-access-log: uvicorn's access log starts every line with the client IP and
# would keep it in the container log (ADR-017). The app's own request log
# records the route, method and status without it.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
