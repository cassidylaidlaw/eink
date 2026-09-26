FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-inter curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps --only-shell chromium

# Tabler icon webfont, vendored at build time so rendering needs no network.
ARG TABLER_VERSION=3.35.0
RUN mkdir -p /srv/vendor/tabler/fonts \
 && curl -fsSL -o /srv/vendor/tabler/tabler-icons.min.css \
      https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@${TABLER_VERSION}/dist/tabler-icons.min.css \
 && curl -fsSL -o /srv/vendor/tabler/fonts/tabler-icons.woff2 \
      https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@${TABLER_VERSION}/dist/fonts/tabler-icons.woff2

COPY config.yaml .
COPY app ./app

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
