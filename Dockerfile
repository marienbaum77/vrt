# Visual regression testing: capture (Playwright/Chromium) + diff engine + CI adapter + API service.
# The Playwright image ships the browsers; fonts are installed explicitly so renders are identical
# on every machine that uses this image (font differences are the main source of false positives).
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PYTHONPATH=/app \
    VRT_REPORTS_DIR=/data/server-reports \
    VRT_CONFIG_DIR=/app/examples

RUN apt-get update \
 && apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core fonts-liberation fonts-noto-color-emoji curl \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

WORKDIR /app
COPY requirements.txt /app/
RUN pip install -r requirements.txt

COPY core /app/core
COPY capture /app/capture
COPY diff_engine /app/diff_engine
COPY ci_adapter /app/ci_adapter
COPY metrics /app/metrics
COPY test_dataset /app/test_dataset
COPY experiments /app/experiments
COPY examples /app/examples
COPY results /app/results

# `vrt` entry point without installing the package
RUN printf '#!/bin/sh\nexec python -m core "$@"\n' > /usr/local/bin/vrt && chmod +x /usr/local/bin/vrt \
 && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8000

# default: the API / webhook service; override the command to run `vrt baseline|test|...`
CMD ["vrt", "serve", "--host", "0.0.0.0", "--port", "8000"]
