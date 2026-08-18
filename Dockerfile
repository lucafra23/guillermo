# Use an official lightweight Python image.
FROM python:3.11-slim

# Allow statements and log messages to immediately appear in the Knative logs
ENV PYTHONUNBUFFERED True

# Set the working directory in the container
ENV APP_HOME /app
WORKDIR $APP_HOME

# Install system dependencies (needed for many Python packages)
# fonts-dejavu-core supplies /usr/share/fonts/truetype/dejavu/DejaVuSans{,-Bold}.ttf,
# which is the lettering fallback face. Without it Pillow drops to a bitmap font and
# the panels render in something nobody chose.
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    fonts-dejavu-core \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy local code to the container image
COPY . .

# Licensed lettering faces are never vendored -- mount them here and set
# LETTERING_FONT_DIR=/app/fonts. Empty by default; DejaVu above is the fallback.
RUN mkdir -p /app/fonts

# Collected once at build so the admin has its CSS with DEBUG off. STATIC_ROOT is
# inside the image; nothing here reads the database.
RUN DJANGO_SECRET_KEY=build-time-only python manage.py collectstatic --noinput

# Cloud Run and Knative inject PORT; this default is for a plain `docker run`.
ENV PORT 8080

# Run the web service on container startup, via gunicorn.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 project.wsgi:application