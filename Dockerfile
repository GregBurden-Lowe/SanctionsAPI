# Use an official lightweight Python image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory inside container
WORKDIR /app

# Create the data directory for your parquet + logs
RUN mkdir -p /app/data

# Copy requirements first (for caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app code
COPY . .

# Expose the FastAPI port
EXPOSE 4512

# Command to run the API
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "4512"]
