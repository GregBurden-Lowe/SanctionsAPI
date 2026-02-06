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
COPY api_server.py utils.py ./
COPY design.json ./

# Built frontend (from stage 1)
COPY --from=frontend-build /build/dist ./frontend/dist

# Persistent data (parquet, search log); create so app can write (relative path "data" from /app)
RUN mkdir -p /app/data

# DigitalOcean / many platforms set PORT; default 8000
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn api_server:app --host 0.0.0.0 --port ${PORT}"]
