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
    PATH="/app/web/server/.venv/bin:${PATH}"
USER app
WORKDIR /app

# 要執行的模組各裝一個 .venv：web、pipeline，以及 skill 直接呼叫的 agent、shared
# 先只裝第三方依賴，讓只動程式碼時這層走快取
COPY --chown=app:app .python-version ./
COPY --chown=app:app packages/shared/pyproject.toml packages/shared/uv.lock ./packages/shared/
COPY --chown=app:app packages/agent/pyproject.toml packages/agent/uv.lock ./packages/agent/
COPY --chown=app:app packages/notify/pyproject.toml packages/notify/uv.lock ./packages/notify/
COPY --chown=app:app apps/pipeline/pyproject.toml apps/pipeline/uv.lock ./apps/pipeline/
COPY --chown=app:app web/server/pyproject.toml web/server/uv.lock ./web/server/
RUN for p in packages/shared packages/agent apps/pipeline web/server; do \
      uv sync --locked --no-install-project --no-install-local --project "$p" || exit 1; done
COPY --chown=app:app packages ./packages
COPY --chown=app:app apps ./apps
COPY --chown=app:app web/server ./web/server
RUN for p in packages/shared packages/agent apps/pipeline web/server; do \
      uv sync --locked --project "$p" || exit 1; done

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
