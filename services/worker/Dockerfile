# The worker shares the API code base (same models, same RLS-aware helpers); only the entrypoint differs.
# Build from the repository root:  docker build -f services/worker/Dockerfile -t routepilot-worker .
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY services/api/requirements.lock ./
RUN pip install -r requirements.lock
COPY services/api/ .
USER app
CMD ["python", "-m", "app.worker"]
