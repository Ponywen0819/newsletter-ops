# syntax=docker/dockerfile:1
# 一個 Dockerfile、兩個映像：--target web、--target worker。各自只帶自己的 venv，不帶原始碼與 uv。
ARG PYTHON=python:3.11-slim-bookworm

FROM node:22-slim AS ui
WORKDIR /web
# 不要整個 COPY web/，會把 host 的 node_modules 蓋過來
COPY apps/web-ui/package.json apps/web-ui/package-lock.json ./
RUN npm ci
COPY apps/web-ui/index.html apps/web-ui/tsconfig.json apps/web-ui/vite.config.ts ./
COPY apps/web-ui/src ./src
COPY apps/web-ui/public ./public
RUN npm run build

# 每個 app 裝成不可編輯的 venv（/opt/<app>）；執行階段的 Python 與這裡同一個映像，venv 直接搬過去就能用
FROM ${PYTHON} AS build
# 與 CI 的 uv 版本一致
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /src
COPY .python-version ./
COPY packages/shared/pyproject.toml packages/shared/uv.lock ./packages/shared/
COPY packages/llm/pyproject.toml packages/llm/uv.lock ./packages/llm/
COPY packages/collect/pyproject.toml packages/collect/uv.lock ./packages/collect/
COPY packages/writer/pyproject.toml packages/writer/uv.lock ./packages/writer/
COPY packages/notify/pyproject.toml packages/notify/uv.lock ./packages/notify/
COPY apps/worker/pyproject.toml apps/worker/uv.lock ./apps/worker/
COPY apps/web-server/pyproject.toml apps/web-server/uv.lock ./apps/web-server/
# 先只裝第三方依賴，讓只動程式碼時這層走快取
RUN for app in web-server worker; do \
      UV_PROJECT_ENVIRONMENT=/opt/$app uv sync --locked --no-editable --no-install-project --no-install-local \
        --project apps/$app || exit 1; done
COPY packages ./packages
COPY apps ./apps
RUN for app in web-server worker; do \
      UV_PROJECT_ENVIRONMENT=/opt/$app uv sync --locked --no-editable --project apps/$app || exit 1; done

FROM ${PYTHON} AS base
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app /var/lib/newsletter/reports /var/lib/newsletter/data \
             /var/lib/newsletter/state /var/lib/newsletter/logs \
 && chown -R app:app /app /var/lib/newsletter
ENV TZ=Asia/Taipei PYTHONUNBUFFERED=1
USER app
WORKDIR /app
COPY --chown=app:app config ./config
COPY --chown=app:app docker ./docker
# 程式以工作目錄（/app）為資料根目錄，這些目錄連到 volume
RUN for d in reports data state logs; do ln -s "/var/lib/newsletter/$d" "/app/$d"; done
VOLUME /var/lib/newsletter
ENTRYPOINT ["/app/docker/entrypoint.sh"]

FROM base AS web
COPY --from=build /opt/web-server /opt/web-server
COPY --from=ui --chown=app:app /web/dist ./apps/web-ui/dist
ENV PATH="/opt/web-server/bin:${PATH}"
CMD ["newsletter-web", "--host", "127.0.0.1", "--port", "8787"]

FROM base AS worker
COPY --from=build /opt/worker /opt/worker
COPY --chown=app:app .claude ./.claude
ENV PATH="/opt/worker/bin:${PATH}"
CMD ["worker", "--port", "8790"]
