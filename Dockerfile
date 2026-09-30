FROM python:3.12-slim

LABEL org.opencontainers.image.title="Alltagswächter"
LABEL org.opencontainers.image.description="Push notifications for Swiss everyday life: your bus is late, rain at 5 pm, cardboard tomorrow."
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/doodelidodo/alltagswaechter"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG=/config/config.toml \
    STATE_FILE=/data/state.json \
    TZ=Europe/Zurich

# No runtime dependencies besides the standard library. tzdata only in case the
# base image ever ships without /usr/share/zoneinfo.
RUN pip install --no-cache-dir tzdata \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /config \
    && chown app:app /data

WORKDIR /app
COPY app ./app
COPY config.example.toml ./

# The image is only built if the self-test passes.
RUN python -m app selftest

USER app
VOLUME ["/data"]

# The loop writes a heartbeat every 30 s.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/alltagswaechter.heartbeat') < 180 else 1)"

CMD ["python", "-m", "app"]
