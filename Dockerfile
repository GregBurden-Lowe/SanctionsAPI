# Base image with Python
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential curl

# Copy requirement files
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy app source code
COPY . .

# Expose the port Cloud Run expects
EXPOSE 8080

# Run the app using uvicorn on the correct port
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8080"]
