FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# fonts-dejavu-core: every accent and symbol in the PDF reports (n°20).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

# Speaker diarization models (n°8), fetched and checksum-verified at build time.
COPY scripts/fetch_diarization_models.py /tmp/
RUN python /tmp/fetch_diarization_models.py /opt/models/diarization

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
