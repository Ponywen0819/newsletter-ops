# syntax=docker/dockerfile:1
# 一個 Dockerfile、兩個映像：--target web、--target worker。各自只帶自己的 venv，不帶原始碼與 uv。
ARG PYTHON=python:3.11-slim-bookworm

# 前端的產物是靜態檔，與架構無關：在建置機的原生架構上建（arm64 映像在 x64 上建時不必經 QEMU 模擬 node）
FROM --platform=$BUILDPLATFORM node:22-slim AS ui
WORKDIR /web
# 不要整個 COPY web/，會把 host 的 node_modules 蓋過來
COPY apps/web-ui/package.json apps/web-ui/package-lock.json ./
RUN npm ci
COPY apps/web-ui/index.html apps/web-ui/tsconfig.json apps/web-ui/vite.config.ts ./
COPY apps/web-ui/src ./src
COPY apps/web-ui/public ./public
RUN npm run build

# 每個 app 裝成不可編輯的 venv（/opt/<app>）；執行階段的 Python 與這裡同一個映像，venv 直接搬過去就能用。
# web、worker 各一個建置階段：--target web 只建 web-server 的 venv（release 的兩個 job 不必都把兩個 venv 建一遍）
FROM ${PYTHON} AS build
# 與 CI 的 uv 版本一致
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /src
COPY .python-version ./

# 各自只 COPY 自己會用到的模組的 pyproject.toml（鎖檔只用 app 自己的 uv.lock）：改 worker 那邊的依賴不會讓 web 的依賴層失效，反之亦然
FROM build AS build-web
COPY packages/shared/pyproject.toml ./packages/shared/
COPY packages/llm/pyproject.toml ./packages/llm/
COPY apps/web-server/pyproject.toml apps/web-server/uv.lock ./apps/web-server/
# 先只裝第三方依賴，讓只動程式碼時這層走快取
RUN UV_PROJECT_ENVIRONMENT=/opt/web-server uv sync --locked --no-dev --no-editable --no-install-project --no-install-local \
      --project apps/web-server
# 只帶 web-server 依賴的模組：改 collect、writer 不會讓 web 映像重建
COPY packages/shared ./packages/shared
COPY packages/llm ./packages/llm
COPY apps/web-server ./apps/web-server
RUN UV_PROJECT_ENVIRONMENT=/opt/web-server uv sync --locked --no-dev --no-editable --project apps/web-server

FROM build AS build-worker
COPY packages/shared/pyproject.toml ./packages/shared/
COPY packages/llm/pyproject.toml ./packages/llm/
COPY packages/collect/pyproject.toml ./packages/collect/
COPY packages/writer/pyproject.toml ./packages/writer/
COPY packages/notify/pyproject.toml ./packages/notify/
COPY apps/worker/pyproject.toml apps/worker/uv.lock ./apps/worker/
RUN UV_PROJECT_ENVIRONMENT=/opt/worker uv sync --locked --no-dev --no-editable --no-install-project --no-install-local \
      --project apps/worker
COPY packages ./packages
COPY apps/worker ./apps/worker
RUN UV_PROJECT_ENVIRONMENT=/opt/worker uv sync --locked --no-dev --no-editable --project apps/worker

FROM ${PYTHON} AS base
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app /var/lib/newsletter/reports /var/lib/newsletter/data \
             /var/lib/newsletter/state /var/lib/newsletter/logs \
 && chown -R app:app /app /var/lib/newsletter
ENV TZ=Asia/Taipei PYTHONUNBUFFERED=1
USER app
WORKDIR /app
COPY --chown=app:app docker ./docker
# 程式以工作目錄（/app）為資料根目錄，這些目錄連到 volume
RUN for d in reports data state logs; do ln -s "/var/lib/newsletter/$d" "/app/$d"; done
VOLUME /var/lib/newsletter
ENTRYPOINT ["/app/docker/entrypoint.sh"]

# config 最常改、又最小，放在各映像的最後：只改設定時，前面的 venv 層不必重建、重推
FROM base AS web
COPY --from=build-web /opt/web-server /opt/web-server
COPY --from=ui --chown=app:app /web/dist ./apps/web-ui/dist
ENV PATH="/opt/web-server/bin:${PATH}"
CMD ["newsletter-web", "--host", "127.0.0.1", "--port", "8787"]
COPY --chown=app:app config ./config

FROM base AS worker
COPY --from=build-worker /opt/worker /opt/worker
COPY --chown=app:app .claude ./.claude
ENV PATH="/opt/worker/bin:${PATH}"
CMD ["worker", "--port", "8790"]
COPY --chown=app:app config ./config
