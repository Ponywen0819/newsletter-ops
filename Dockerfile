# syntax=docker/dockerfile:1
# web 與排程共用同一個映像；前端在第一階段建置，最終映像只帶走 dist
FROM node:22-slim AS web
WORKDIR /web
# 不要整個 COPY web/，會把 host 的 node_modules 蓋過來
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci
COPY web/ui/index.html web/ui/tsconfig.json web/ui/vite.config.ts ./
COPY web/ui/src ./src
COPY web/ui/public ./public
RUN npm run build

FROM python:3.11-slim-bookworm

# 與 CI 的 uv 版本一致
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /uvx /usr/local/bin/

RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app /var/lib/newsletter/reports /var/lib/newsletter/data \
             /var/lib/newsletter/state /var/lib/newsletter/logs \
 && chown -R app:app /app /var/lib/newsletter

ENV TZ=Asia/Taipei \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"
USER app
WORKDIR /app

# 先只裝第三方依賴，讓只動程式碼時這層走快取
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
COPY --chown=app:app shared/pyproject.toml ./shared/pyproject.toml
COPY --chown=app:app agent/pyproject.toml ./agent/pyproject.toml
COPY --chown=app:app notify/pyproject.toml ./notify/pyproject.toml
COPY --chown=app:app web/server/pyproject.toml ./web/server/pyproject.toml
COPY --chown=app:app pipeline/pyproject.toml ./pipeline/pyproject.toml
RUN uv sync --locked --no-install-workspace
COPY --chown=app:app shared ./shared
COPY --chown=app:app agent ./agent
COPY --chown=app:app notify ./notify
COPY --chown=app:app web/server ./web/server
COPY --chown=app:app pipeline ./pipeline
RUN uv sync --locked

COPY --chown=app:app run_daily.sh ./
COPY --from=web --chown=app:app /web/dist ./web/ui/dist
COPY --chown=app:app config ./config
COPY --chown=app:app .claude ./.claude
COPY --chown=app:app docker ./docker

# 程式以相對路徑找這些目錄，連到 volume
RUN for d in reports data state logs; do ln -s "/var/lib/newsletter/$d" "/app/$d"; done
VOLUME /var/lib/newsletter

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["newsletter-web", "--host", "127.0.0.1", "--port", "8787"]
