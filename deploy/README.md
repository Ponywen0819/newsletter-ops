# 部署到家用 host（Docker + Cloudflare Tunnel + Access）

目標：worker 與 web 跑在家裡一台 host（VM 也行，只要有 Docker），經 Cloudflare 在外也能看晨報、按有用／沒用。本機只當測試區。
host 上不用裝 Python、uv、cloudflared，也不用開任何對外 port。專案全貌見[根目錄 README](../README.md)。

```
手機／筆電 ──https──▶ Cloudflare（Access：只放行你的 email）──Tunnel──▶ cloudflared 容器 ──http://web:8787──▶ web 容器
                                                                    worker 容器：每天 08:00 跑每日流程（自己的 be 網路，web、cloudflared 連不到）
                                    三個容器共用一個 volume：newsletter-data（reports／data／state／logs）
```

- `Dockerfile`：一個檔、兩個 target，建出兩個映像：`newsletter-ops-web`（`--target web`）與 `newsletter-ops-worker`（`--target worker`）。
  各自只帶自己 app 的 venv（`/opt/web-server`、`/opt/worker`，用 uv 鎖定的依賴、不可編輯安裝），**不帶原始碼、也沒有 uv**，指令直接在 PATH 上；
  非 root 執行，兩邊共用 base 層（使用者、`config/`、volume 連結）。web 另外帶前端：先用 Node 建置 `apps/web-ui/`，只把 `dist` 帶進映像，
  所以 host 與映像裡都不需要 Node；worker 另外帶 `.claude/`（寫報告的 skill）。`.dockerignore` 是白名單，前端原始碼與 `apps/web-ui/public/`（圖示）要放行才進得了 build context。
- `docker-compose.yml`：`web`、`worker`、`cloudflared` 三個服務與 volume。`worker` 跑 `worker`（見 [apps/worker/README.md](../apps/worker/README.md)），
  它的 `/status`、`/run` 不 publish，而且只在 `be` 網路：`/run` 會燒額度、寄信，沒有登入的 `web` 與對外的 `cloudflared` 都碰不到。`cloudflared` 用 Tunnel token 執行，
  不需要 `cert.pem`、憑證檔或 `config.yml`；對外的主機名稱在 Cloudflare 後台設定。
- `newsletter-web` 沒有登入、而且能寫入 `state/feedback.jsonl`，**唯一的防線是 Access**。主 port（8787）不 publish，host 不會對外開它；
  請不要為了方便從 host 直接開而 publish 它，那會繞過 Access（host 本機仍連得到容器的內部 IP，所以這台 host 本身要信得過）。Tunnel 的網域一定要先掛上 Access 再對外使用（步驟 2、4）。
- **管理 port（8788）**是唯一 publish 的 port，只綁 host 的 `127.0.0.1`：貼 OAuth token 的 `/auth` 頁面只在這個 port 開，主 port 上永遠是 404。
  它沒有登入，靠「只有 host 本機連得到」擋人，所以不要改成 `"8788:8788"`（會對區網開放），也不要放進 Tunnel 的 Public Hostname。
  萬一 Tunnel 誤指到它，Cloudflare 帶的 `Cf-*` 標頭與公網 `Host` 仍會被拒（`/auth` 照樣 404），但那只是第二道防線。
- 機密放 `.env`（Gmail 帳密、`TUNNEL_TOKEN`）：`web` 容器拿不到這些，`worker` 拿不到 `TUNNEL_TOKEN`。Claude 的 OAuth token 另外存在 volume 的 `state/oauth_token.json`（權限 600），`web` 負責驗證並儲存、`worker` 讀取。

`docker-autodeploy.sh` 與 `newsletter-autodeploy.{service,timer}` 是自動部署（步驟 6）；其他檔案是**不用 Docker 時**的範本：systemd 單元、cloudflared 設定、env 範本；`check_boundaries.py` 是模組依賴方向檢查（CI 也跑），與部署無關。

**1. 取得程式與機密**

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops && cd ~/newsletter-ops
cp .env.example .env && chmod 600 .env
$EDITOR .env      # GMAIL_USER、GMAIL_APP_PASSWORD；TUNNEL_TOKEN 在下一步取得
```

Claude 的 OAuth token（`claude setup-token` 取得，效期一年，只走訂閱額度，見 [llm/README.md](../packages/llm/README.md)「認證」）**不放 `.env`、也不讀環境變數**：
放環境變數的話，一年後換新就得改檔並重新部署。它存在 volume 的 `state/oauth_token.json`，換新立刻生效，做法在步驟 3 之後的「貼上 OAuth token」。

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
docker compose ps        # web 要是 healthy、cloudflared 是 Up；PORTS 欄只有 web 的 127.0.0.1:8788->8788
```

`.env` 缺 `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`TUNNEL_TOKEN` 任何一個，`docker compose` 會直接報錯並指出缺哪個。

**貼上 OAuth token**（第一次、以及一年後 `newsletter-write` exit 5 或網頁頂端出現「Claude 授權已失效」時）：在**自己的電腦**執行 `claude setup-token`、
在瀏覽器完成授權，複製它印出的 token（只會印一次；需要 Pro／Max／Team／Enterprise），再用 **host 本機的瀏覽器**開 <http://127.0.0.1:8788/auth>，
貼上、按「驗證並儲存」。host 在遠端的話：`ssh -L 8788:127.0.0.1:8788 <host>`，再用自己電腦的瀏覽器開 <http://localhost:8788/auth>。
`/auth` 只在管理 port 開，從 Tunnel 網域（主 port）開不了；那裡頁面頂端的橫幅只會寫「請在主機上重新授權」。
伺服器會先實際呼叫一次 Claude 驗證（用掉極少的訂閱額度），通過才存進 volume；舊的 token 不會被無效的覆蓋。
不用重啟、不用重新部署，worker 下一次執行就讀到新的（今天已經因此失敗的話，要手動補跑，見步驟 5）。沒做這一步的話，每天的排程會以 exit 2 結束（沒有 OAuth token）。

**4. 驗收**

| 檢查 | 預期 |
| --- | --- |
| `docker compose ps` | `web`、`worker` healthy、`cloudflared` Up、PORTS 欄只有 web 的 `127.0.0.1:8788->8788/tcp` |
| `docker compose port web 8788` | `127.0.0.1:8788`（**不能是** `0.0.0.0:8788`） |
| host 上 `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8788/auth` | `200`（管理 port 的貼 token 頁） |
| 登入 Access 後開 `https://news.example.com/auth` | 「找不到頁面」（404），不是貼 token 的頁面 |
| `docker compose logs cloudflared` | 出現 `Registered tunnel connection`（沒有就是 token 有誤或出站連不到 Cloudflare） |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報（還沒有報告時是「還沒產出」頁） |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `newsletter-web`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | `docker compose exec web tail -n1 state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照 [apps/web-ui/README.md](../apps/web-ui/README.md)「PWA」一節的「正式站驗收」表 |
| `.env` 設了 `NEWSLETTER_BASE_URL` 後寄一封信，在手機點信裡的「👍 有用」連結 | 先經 Access 登入，再看到「確認標為 有用」頁；**這時 `feedback.jsonl` 還沒變**，按了確認才多一行 |
| `docker network inspect newsletter-ops_be --format '{{range .Containers}}{{.Name}} {{end}}'` | 只有 worker 的容器 |
| `docker compose run --rm worker newsletter-llm --auth-check` | 只驗證 OAuth token（一次最小的呼叫，不跑晨報），通過才表示每天的排程跑得起來 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，先手動跑一次（見下，會呼叫 Claude、用掉訂閱額度）。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。

**5. 日常操作**

```bash
docker compose logs -f worker                  # 排程輸出：下次執行時間、開始、結束；失敗時附上 logs/ 的最後 20 行
docker compose exec worker python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8790/status').read().decode())"   # 是否在跑、下次幾點、上次結果
docker compose exec worker python -c "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8790/run', method='POST')).read().decode())"   # 立刻跑一次（記成 manual；執行中回 409）
docker compose exec web ls reports                # volume 裡的報告
docker compose run --rm -e NEWSLETTER_DEBUG=1 -e NEWSLETTER_RUN_LABEL=test worker newsletter-write   # 只產報告、不寄信（要先有當日 curated JSON）
git pull && docker compose up -d --build          # 手動更新（interests.md、config/、程式都在映像裡，要重 build）
```

- 每天 08:00（`config/config.json` 的時區，`.env` 的 `NEWSLETTER_RUN_AT` 可改）。容器啟動時，今天過了時間卻還沒跑過（或上次跑到一半被停掉）就補跑一次；
  只補同一天，跨日就算了。今天跑過但失敗了**不會**自動重跑（避免 token 失效時每次重啟都燒一次），修好後用上面的 `/run` 補。
  手動跑的也算「今天跑過」，所以 08:00 前手動跑完，08:00 就不會再寄一次。`docker stop` 會等執行中的步驟收尾（最多 2 分鐘）。
- `up -d --build` 只會重建有變動的容器；`web` 當掉或被重建時，`cloudflared` 靠服務名稱 `web` 重新連上，不必另外處理。

**6. 自動部署（GHCR）**

`main` 每次更新、`selftest` 通過後，[release.yml](../.github/workflows/release.yml) 會建兩個映像推到
`ghcr.io/ponywen0819/newsletter-ops-web` 與 `ghcr.io/ponywen0819/newsletter-ops-worker`，tag 都是 `latest` 與 `sha-<commit>`，只建 `linux/arm64`。
host 上的 timer 每 10 分鐘拉一次，有新版才重建容器。

```bash
# package 是 private 時才需要；PAT 只要 read:packages
echo <PAT> | docker login ghcr.io -u Ponywen0819 --password-stdin

mkdir -p ~/.config/systemd/user
cp ~/newsletter-ops/deploy/newsletter-autodeploy.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newsletter-autodeploy.timer
sudo loginctl enable-linger "$USER"   # 沒登入也持續執行；執行的使用者要在 docker 群組
```

- 每日流程正在跑時這輪會跳過。
- 看紀錄：`journalctl --user -u newsletter-autodeploy`；馬上更新一次：`systemctl --user start newsletter-autodeploy.service`。
- **退版**：`.env` 加 `NEWSLETTER_TAG=sha-<commit>`，然後 `docker compose up -d`；要回到自動跟新版就把那行刪掉。
- timer 只更新映像；`docker-compose.yml` 改了要自己 `git pull`。
- **從單一映像換過來時**（舊的 `ghcr.io/ponywen0819/newsletter-ops`）：先 `git pull` 拿到新的 compose，再 `docker compose up -d`；
  確認兩個容器都 healthy 後，`docker image rm ghcr.io/ponywen0819/newsletter-ops:latest` 刪掉舊映像（不會自己刪）。

Actions 不能跑時（例如額度用完）可以在自己的電腦建好推上去，結果和 release.yml 一樣。需要 `write:packages` 的 classic PAT：

```bash
echo <PAT> | docker login ghcr.io -u Ponywen0819 --password-stdin
git switch --detach origin/main
for t in web worker; do docker buildx build --platform linux/arm64 --target "$t" --push \
  -t "ghcr.io/ponywen0819/newsletter-ops-$t:latest" -t "ghcr.io/ponywen0819/newsletter-ops-$t:sha-$(git rev-parse HEAD)" . || break; done
```

**7. 資料保存：不保存**

生成物都在 volume `newsletter-data`（容器內 `/var/lib/newsletter`），換容器、換映像、`docker compose down` 都還在；
**`docker compose down -v` 或 `docker volume rm` 才會刪掉**。不備份，決定見[根目錄 README](../README.md)「資料保存」。

## 不用 Docker：systemd + cloudflared

不想用 Docker 時，直接在 host 上跑。單元檔與設定範本就在本目錄，以下假設 repo 在 `~/newsletter-ops`，需要 [uv](https://docs.astral.sh/uv/)
（見[根目錄 README](../README.md)「環境（uv）」）與 Node（^20.19 或 >=22.12，只在建置網頁前端時用到，之後執行不需要）。資料保存的決定同上：生成物留在 host 的 `reports/`、`data/`、`state/`、`logs/`，不備份、不進版控。

**1. 取得程式**

先裝 [uv](https://docs.astral.sh/uv/) 與 Node。

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops
cd ~/newsletter-ops && for p in apps/worker apps/web-server; do uv sync --locked --project $p; done   # 建各自的 .venv、裝 claude-agent-sdk；沒有 Python 3.11 時 uv 會自己下載
(cd apps/web-ui && npm ci && npm run build)          # 建置網頁前端到 apps/web-ui/dist；沒做的話 web service 的頁面都回 503
uv run --project apps/worker newsletter-collect --list-sources                 # 不連網，確認設定可用
```

**2. 機密**

```bash
mkdir -p ~/.config/newsletter-ops
cp ~/newsletter-ops/deploy/newsletter.env.example ~/.config/newsletter-ops/env
chmod 600 ~/.config/newsletter-ops/env
$EDITOR ~/.config/newsletter-ops/env   # GMAIL_USER、GMAIL_APP_PASSWORD；systemd 的 EnvironmentFile 語法，值含空格要加引號、不支援 $VAR
```

只有 worker service 會載入這個檔；web service 不載入，網頁程序拿不到這些機密。Claude 的 OAuth token 不放這裡：web 啟動後（下面的步驟 3）開 `/auth` 貼上，存成 `state/oauth_token.json`。

**3. systemd（user 單元，不需要 root）**

```bash
mkdir -p ~/.config/systemd/user
cp ~/newsletter-ops/deploy/newsletter-{web,worker}.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newsletter-web.service newsletter-worker.service
sudo loginctl enable-linger "$USER"   # 沒登入也持續執行，開機自動啟動
```

- 貼 Claude 的 OAuth token：systemd 部署沒有管理 port，`/auth` 就在主 port，在 host 上開 `http://127.0.0.1:8787/auth`（遠端 host 用 `ssh -L 8787:127.0.0.1:8787 <host>` 後開 `http://localhost:8787/auth`），做法見 [llm/README.md](../packages/llm/README.md)「認證」。沒貼的話排程會以 exit 2 結束。
- worker 和 Docker 版是同一個程式，排程、補跑、`/status`、`/run` 的行為見上面步驟 5；`/status`、`/run` 只綁 `127.0.0.1:8790`。
- 看 web：`journalctl --user -u newsletter-web -f`；看排程：`journalctl --user -u newsletter-worker -f`（下次執行時間、開始、結束），細節在 `logs/<YYYY-MM>.log`。
- 手動跑一次：`curl -X POST http://127.0.0.1:8790/run`（會真的呼叫 Claude 並寄信，約數分鐘、用掉訂閱額度）；結果看 `curl -s http://127.0.0.1:8790/status` 的 `last`。
- 更新（`interests.md`、`config/`、程式等被追蹤的檔案）：
  `git -C ~/newsletter-ops pull && (cd ~/newsletter-ops && uv sync --locked --project apps/worker && uv sync --locked --project apps/web-server && cd apps/web-ui && npm ci && npm run build) && systemctl --user restart newsletter-web newsletter-worker`。
  （前端沒變動時，`npm ci && npm run build` 可以省略。）`restart` 會等執行中的每日流程收尾（最多 2 分鐘）。

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
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `newsletter-web`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | host 上 `tail -n1 ~/newsletter-ops/state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照 [apps/web-ui/README.md](../apps/web-ui/README.md)「PWA」一節的「正式站驗收」表 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，在 host 上依序跑 `uv run --project apps/worker newsletter-collect`、
`uv run --project apps/worker newsletter-write`（或上面的手動排程）先產一份。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。
