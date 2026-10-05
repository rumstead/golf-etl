FROM python:3.12-slim AS base
LABEL org.opencontainers.image.source=https://github.com/rumstead/golf-etl
# libgl1 and libglib2.0 for opencv, libegl1 and libgles2 for mediapipe
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libegl1 libgles2 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# rclone talks to Google Drive with its own verified OAuth app, so no Google Cloud project is needed
ARG RCLONE_VERSION=v1.75.1
ADD https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-linux-amd64.zip /tmp/rclone.zip
RUN python -m zipfile -e /tmp/rclone.zip /tmp/rclone \
    && install -m 755 /tmp/rclone/rclone-${RCLONE_VERSION}-linux-amd64/rclone /usr/local/bin/rclone \
    && rm -rf /tmp/rclone /tmp/rclone.zip
ADD --chmod=644 https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task /opt/golf-etl/pose_landmarker_lite.task
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
RUN ruff check src tests && ruff format --check src tests && python -m pytest -q

FROM base
ARG GOLF_ETL_VERSION=dev
# matplotlib and fontconfig (pulled in by mediapipe) want a writable home for caches
ENV GOLF_ETL_VERSION=$GOLF_ETL_VERSION HOME=/tmp
RUN useradd --uid 10001 --no-create-home golf && mkdir /scratch && chown golf /scratch
USER 10001
ENTRYPOINT ["golf-etl"]
CMD ["poll-drive"]
