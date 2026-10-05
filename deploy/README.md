# 部署到家用 host（Docker + Cloudflare Tunnel + Access）

目標：pipeline 與 web 跑在家裡一台 host（VM 也行，只要有 Docker），經 Cloudflare 在外也能看晨報、按有用／沒用。本機只當測試區。
host 上不用裝 Python、uv、cloudflared，也不用開任何對外 port。專案全貌見[根目錄 README](../README.md)。

```
手機／筆電 ──https──▶ Cloudflare（Access：只放行你的 email）──Tunnel──▶ cloudflared 容器 ──http://web:8787──▶ web 容器
                                                                    scheduler 容器：每天 08:00 跑 run_daily.sh
                                    三個容器共用一個 volume：newsletter-data（reports／data／state／logs）
```

- `Dockerfile`：web 與排程共用同一個映像（Python 3.11 + uv 鎖定的依賴，各成員以 editable 裝進同一個環境，非 root 執行）。多階段建置：先用 Node 建置網頁前端（`web/ui/`），
  只把 `web/ui/dist` 帶進最終映像，所以 host 與映像裡都不需要 Node；`.dockerignore` 是白名單，前端原始碼與 `web/ui/public/`（圖示）要放行才進得了 build context。
- `docker-compose.yml`：`web`、`scheduler`、`cloudflared` 三個服務與 volume。`cloudflared` 用 Tunnel token 執行，
  不需要 `cert.pem`、憑證檔或 `config.yml`；對外的主機名稱在 Cloudflare 後台設定。
- `web.py` 沒有登入、而且能寫入 `state/feedback.jsonl`，**唯一的防線是 Access**。compose 沒有 `ports:`，host 不會開任何 port；
  請不要為了方便從 host 直接開而加上 `ports:`，那會繞過 Access（host 本機仍連得到容器的內部 IP，所以這台 host 本身要信得過）。Tunnel 的網域一定要先掛上 Access 再對外使用（步驟 2、4）。
- 機密只放 `.env`：`web` 容器拿不到任何機密，`scheduler` 拿不到 `TUNNEL_TOKEN`。

本目錄的其他檔案是**不用 Docker 時**的範本：systemd 單元、cloudflared 設定、env 範本；`check_boundaries.py` 是模組依賴方向檢查（CI 也跑），與部署無關。

**1. 取得程式與機密**

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops && cd ~/newsletter-ops
cp .env.example .env && chmod 600 .env
$EDITOR .env      # GMAIL_USER、GMAIL_APP_PASSWORD、CLAUDE_CODE_OAUTH_TOKEN；TUNNEL_TOKEN 在下一步取得
```

`CLAUDE_CODE_OAUTH_TOKEN` 的來源與用法見 [agent/README.md](../agent/README.md)「認證」：在**自己的電腦**執行 `claude setup-token` 取得，效期一年，只走訂閱額度。
Docker 部署**用不了 web 的 `/auth` 頁面**：它只服務「來源是 loopback、沒有代理標頭」的請求，容器網路裡的請求一律 404，
所以 token 放在 `.env`。token 到期（`agent_run.py` exit 5）時重新產生，改 `.env` 後 `docker compose up -d`（scheduler 會用新的環境變數重建）。

**2. Cloudflare：建立 Tunnel 與 Access（Access 一定要做）**

Zero Trust 後台（介面名稱依版本略有不同）：

1. Networks → Tunnels → Create a tunnel → 類型選 Cloudflared，取個名字。畫面上的安裝指令不用理它，
   只複製其中的 token（那串很長的字）貼到 `.env` 的 `TUNNEL_TOKEN`。
2. 該 Tunnel 的 Public Hostname：填你的網域 `news.example.com`，Service 類型選 `HTTP`、URL 填 `web:8787`（compose 服務名稱）。DNS 紀錄會自動建立。
3. Access → Applications → Add an application → Self-hosted：Application domain 填同一個 `news.example.com`；
   Policy 一條就好：Action = Allow，Include = Emails，只填你自己的 email。不要留 Everyone 之類的其他 policy。

**3. 啟動**

```bash
docker compose up -d --build
docker compose ps        # web 要是 healthy、cloudflared 是 Up，PORTS 欄全空
```

`.env` 缺 `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`CLAUDE_CODE_OAUTH_TOKEN`、`TUNNEL_TOKEN` 任何一個，`docker compose` 會直接報錯並指出缺哪個。

**4. 驗收**

| 檢查 | 預期 |
| --- | --- |
| `docker compose ps` | `web` healthy、`cloudflared` Up、PORTS 欄是空的 |
| `docker compose logs cloudflared` | 出現 `Registered tunnel connection`（沒有就是 token 有誤或出站連不到 Cloudflare） |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報（還沒有報告時是「還沒產出」頁） |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `web.py`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | `docker compose exec web tail -n1 state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照 [web/ui/README.md](../web/ui/README.md)「PWA」一節的「正式站驗收」表 |
| `.env` 設了 `NEWSLETTER_BASE_URL` 後寄一封信，在手機點信裡的「👍 有用」連結 | 先經 Access 登入，再看到「確認標為 有用」頁；**這時 `feedback.jsonl` 還沒變**，按了確認才多一行 |
| `docker compose run --rm scheduler uv run newsletter-agent --auth-check` | 只驗證 OAuth token（一次最小的呼叫，不跑晨報），通過才表示每天的排程跑得起來 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，先手動跑一次（見下，會呼叫 Claude、有費用）。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。

**5. 日常操作**

```bash
docker compose logs -f scheduler                  # 排程輸出；run_daily.sh 失敗時會附上 logs/ 的最後 20 行
docker compose exec web ls reports                # volume 裡的報告
docker compose run --rm scheduler ./run_daily.sh  # 手動跑一次完整流程（抓取 → agent → render → 寄信）
docker compose run --rm -e NEWSLETTER_DEBUG=1 -e NEWSLETTER_RUN_LABEL=test scheduler uv run newsletter-agent   # 只產報告、不寄信
git pull && docker compose up -d --build          # 更新（interests.md、config/、程式都在映像裡，要重 build）
```

- 每天 08:00（Asia/Taipei，`.env` 的 `NEWSLETTER_RUN_AT` 可改）。容器停機時錯過的那一次不會補跑，要補就手動跑。
- 每個**新**容器的第一次 `uv run` 會在 log 印出 `Building … Uninstalled 4 packages / Installed 4 packages`：uv 判定 editable 成員比安裝記錄新而重裝
  （約 10 毫秒、不需要網路），之後同一個容器就安靜了。這不是錯誤。
- `up -d --build` 只會重建有變動的容器；`web` 當掉或被重建時，`cloudflared` 靠服務名稱 `web` 重新連上，不必另外處理。

**6. 資料保存：不保存**

生成物都在 volume `newsletter-data`（容器內 `/var/lib/newsletter`），換容器、換映像、`docker compose down` 都還在；
**`docker compose down -v` 或 `docker volume rm` 才會刪掉**。不備份，決定見[根目錄 README](../README.md)「資料保存」。

## 不用 Docker：systemd + cloudflared

不想用 Docker 時，直接在 host 上跑。單元檔與設定範本就在本目錄，以下假設 repo 在 `~/newsletter-ops`，需要 [uv](https://docs.astral.sh/uv/)
（見[根目錄 README](../README.md)「環境（uv）」）與 Node（^20.19 或 >=22.12，只在建置網頁前端時用到，之後執行不需要）。資料保存的決定同上：生成物留在 host 的 `reports/`、`data/`、`state/`、`logs/`，不備份、不進版控。

**1. 取得程式**

先裝 [uv](https://docs.astral.sh/uv/) 與 Node。

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops
cd ~/newsletter-ops && uv sync --locked      # 建 .venv、裝 claude-agent-sdk；沒有 Python 3.11 時 uv 會自己下載
(cd web/ui && npm ci && npm run build)          # 建置網頁前端到 web/ui/dist；沒做的話 web service 的頁面都回 503
uv run newsletter-fetch --list-sources             # 不連網，確認設定可用
```

**2. 機密**

```bash
mkdir -p ~/.config/newsletter-ops
cp ~/newsletter-ops/deploy/newsletter.env.example ~/.config/newsletter-ops/env
chmod 600 ~/.config/newsletter-ops/env
$EDITOR ~/.config/newsletter-ops/env   # GMAIL_USER、GMAIL_APP_PASSWORD、CLAUDE_CODE_OAUTH_TOKEN（或之後開 /auth 貼）
```

只有排程入口會載入這個檔；web service 不載入，網頁程序拿不到這些機密。

**3. systemd（user 單元，不需要 root）**

```bash
mkdir -p ~/.config/systemd/user
cp ~/newsletter-ops/deploy/newsletter-{web.service,daily.service,daily.timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newsletter-web.service newsletter-daily.timer
sudo loginctl enable-linger "$USER"   # 沒登入也持續執行，開機自動啟動
```

- 每天 08:00（Asia/Taipei）跑 `run_daily.sh`；host 當時關機的話，開機後補跑（`Persistent=true`）。
- 看 web：`journalctl --user -u newsletter-web -f`；看排程：`systemctl --user list-timers`、`logs/<YYYY-MM>.log`。
- 手動跑一次排程：`systemctl --user start newsletter-daily.service`（會真的呼叫 Claude 並寄信，約數分鐘、有 API 費用），
  失敗時 `systemctl --user status newsletter-daily` 會顯示 failed，原因在 `logs/<YYYY-MM>.log`。
- 更新（`interests.md`、`config/`、程式等被追蹤的檔案）：
  `git -C ~/newsletter-ops pull && (cd ~/newsletter-ops && uv sync --locked && cd web/ui && npm ci && npm run build) && systemctl --user restart newsletter-web`。
  （前端沒變動時，`npm ci && npm run build` 可以省略。）排程每次都重新讀檔，不必重啟。

**4. cloudflared Tunnel**

照 Cloudflare 文件安裝 `cloudflared`，然後：

```bash
cloudflared tunnel login
cloudflared tunnel create newsletter                       # 印出 <TUNNEL_UUID>，憑證寫在 ~/.cloudflared/
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<TUNNEL_UUID>.json /etc/cloudflared/
sudo cp ~/newsletter-ops/deploy/cloudflared-config.yml.example /etc/cloudflared/config.yml   # 改 <TUNNEL_UUID> 與 hostname
cloudflared tunnel route dns newsletter news.example.com
sudo cloudflared service install                           # 以 system service 常駐
```

**5. Cloudflare Access（先做，再對外使用）**

Zero Trust 後台 → Access → Applications → Add an application → Self-hosted：
Application domain 填 `news.example.com`；Policy 一條就好：Action = Allow，Include = Emails，只填你自己的 email。
不要留 Everyone 之類的其他 policy。

**6. 驗收**

| 檢查 | 預期 |
| --- | --- |
| host 上 `curl -sI http://127.0.0.1:8787/reports` | `200` |
| host 上 `ss -ltn \| grep 8787` | 只有 `127.0.0.1:8787`，不是 `0.0.0.0` |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報 |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `web.py`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | host 上 `tail -n1 ~/newsletter-ops/state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照 [web/ui/README.md](../web/ui/README.md)「PWA」一節的「正式站驗收」表 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，在 host 上 `uv run newsletter-agent`
（或上面的手動排程）先產一份。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。
