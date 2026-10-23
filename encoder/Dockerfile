FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY compatibility.py media_encoder.py dashboard.py ./

ENTRYPOINT []
CMD ["python3", "/app/media_encoder.py"]
