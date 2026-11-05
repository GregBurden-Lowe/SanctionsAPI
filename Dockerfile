# Stage 1: build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: run backend + serve built frontend
FROM python:3.12-slim
WORKDIR /app

# Install Python deps (no cache to keep image smaller)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Backend code
COPY api_server.py screening_worker.py ai_triage.py ai_triage_worker.py utils.py retention_report.py job_recovery.py screening_policy.py review_decisions.py screening_history.py screening_db.py auth_db.py security.py ./
COPY acceptance_export.py acceptance_metrics.py source_assurance.py screening_guidance.py screening_monitoring.py company_investigations.py ./
COPY deployment_runtime.py name_matching.py ./
COPY scripts/ ./scripts/
COPY db/ ./db/
COPY services ./services
COPY routes ./routes
COPY migrations ./migrations
COPY design.json ./

# Built frontend (from stage 1)
COPY --from=frontend-build /build/dist ./frontend/dist

# Persistent data (parquet, search log); create so app can write (relative path "data" from /app)
RUN mkdir -p /app/data

# DigitalOcean / many platforms set PORT; default 8000
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn api_server:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 660"]
