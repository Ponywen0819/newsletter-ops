# syntax=docker/dockerfile:1
# newsletter-ops：web 與每日排程共用同一個映像，由 docker-compose.yml 決定跑哪一個。
FROM python:3.11-slim-bookworm

# uv 版本與本機開發用的一致。Python 直接用這個映像的 3.11（對應 .python-version），不讓 uv 另外下載。
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

# 生成物（報告、抓取結果、回饋、log）集中放 /var/lib/newsletter，這是唯一需要 volume 的路徑。
# 非 root 使用者（uid 1000）擁有它；named volume 第一次掛上時會沿用這裡的內容與擁有者。
# tzdata、ca-certificates、bash 與 GNU date 基底映像本來就有，不必再 apt。
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

# 依賴先裝：只動程式碼時這一層走快取
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
RUN uv sync --locked

COPY --chown=app:app run_daily.sh ./
COPY --chown=app:app src ./src
COPY --chown=app:app config ./config
COPY --chown=app:app .claude ./.claude
COPY --chown=app:app docker ./docker

# 程式碼以相對路徑找 reports/、data/、state/、logs/：用符號連結指到 volume，不必改任何程式
RUN for d in reports data state logs; do ln -s "/var/lib/newsletter/$d" "/app/$d"; done
VOLUME /var/lib/newsletter

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# 預設是 web；沒有登入，只 bind 127.0.0.1（對外只能經 cloudflared + Access）
CMD ["python", "src/web.py", "--host", "127.0.0.1", "--port", "8787"]
