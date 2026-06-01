FROM python:3.12-slim

# Dependencias de sistema para WeasyPrint (Pango/Cairo/GLib)
# apt-get install corre post-install scripts que actualizan ldconfig cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libharfbuzz0b \
    libgdk-pixbuf-2.0-0 \
    fonts-dejavu-core \
    fonts-liberation2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
