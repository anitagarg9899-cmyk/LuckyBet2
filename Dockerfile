FROM python:3.13-slim

# Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies (ffmpeg for audio/voice support, build deps for some wheels)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       build-essential \
       libjpeg-dev \
       zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and app directory
RUN useradd -m appuser
WORKDIR /app

# Install Python dependencies (use requirements.txt from the repo)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code and set ownership to the non-root user
COPY --chown=appuser:appuser . /app

USER appuser

# Default command
CMD ["python", "bot.py"]
