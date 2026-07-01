FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (leverages Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Runtime data directory (SQLite DB + encryption key live here)
RUN mkdir -p data

# Default secret; override at `docker run -e FLASK_SECRET_KEY=...`
ENV FLASK_SECRET_KEY=change-me-to-a-random-string
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "app.py"]
