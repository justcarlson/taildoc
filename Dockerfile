FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="Tailplan" \
      org.opencontainers.image.source="https://github.com/justcarlson/taildoc" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TAILPLAN_HOST=0.0.0.0 \
    TAILPLAN_PORT=9127 \
    TAILPLAN_DATA_DIR=/var/lib/tailplan \
    TAILPLAN_TOKEN_FILE=/run/secrets/tailplan_upload_token \
    TAILPLAN_BASE_URL=http://127.0.0.1:9127

RUN groupadd --gid 10001 tailplan \
    && useradd --uid 10001 --gid tailplan --no-create-home --home-dir /var/lib/tailplan --shell /usr/sbin/nologin tailplan \
    && install -d -o tailplan -g tailplan -m 0700 /var/lib/tailplan

WORKDIR /app
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md tailplan_server.py ./
COPY tailplan_themes ./tailplan_themes
COPY --chmod=0755 docker-entrypoint.py /usr/local/bin/tailplan-container-entrypoint
RUN python -m pip install --no-cache-dir --no-compile .

EXPOSE 9127
VOLUME ["/var/lib/tailplan"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/readyz' % os.environ.get('TAILPLAN_PORT', '9127'), timeout=3).read()"]

ENTRYPOINT ["tailplan-container-entrypoint"]
CMD ["tailplan-server"]
