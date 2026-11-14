# 部署到家用 host（Docker + Cloudflare Tunnel + Access）

目標：worker 與 web 跑在家裡一台 host（VM 也行，只要有 Docker），經 Cloudflare 在外也能看晨報、按有用／沒用。測試區是另一台機器上的另一組容器，見[測試區](#測試區區域網路沒有-tunnel)。
host 上不用裝 Python、uv、cloudflared，也不用開任何對外 port。專案全貌見[根目錄 README](../README.md)。

```
手機／筆電 ──https──▶ Cloudflare（Access：只放行你的 email）──Tunnel──▶ cloudflared ──http://nginx:8080──┐
家裡區網／VPN ─────────────────────────────────────────────────────────────▶ host:8788 ─▶ nginx:8788 ──────┤
                                                                                                         ▼
                                                                          web 容器（只在 app 網路，不 publish）
worker 容器：每天 08:00 跑每日流程（be 網路，只有 web 連得到）；web 與 worker 共用一個 volume：newsletter-data
```

- `Dockerfile`：一個檔、兩個 target，建出兩個映像：`newsletter-ops-web`（`--target web`）與 `newsletter-ops-worker`（`--target worker`）。
  各自只帶自己 app 的 venv（`/opt/web-server`、`/opt/worker`，用 uv 鎖定的依賴、不可編輯安裝），**不帶原始碼、也沒有 uv**，指令直接在 PATH 上；
  非 root 執行，兩邊共用 base 層（使用者、`config/`、volume 連結）。web 另外帶前端：先用 Node 建置 `apps/web-ui/`，只把 `dist` 帶進映像，
  所以 host 與映像裡都不需要 Node；worker 另外帶 `.claude/`（寫報告的 skill）。`.dockerignore` 是白名單，前端原始碼與 `apps/web-ui/public/`（圖示）要放行才進得了 build context。
- `docker-compose.yml`：`web`、`worker`、`nginx`、`cloudflared` 四個服務與 volume。`worker` 跑 `worker`（見 [apps/worker/README.md](../apps/worker/README.md)），
  它的 `/status`、`/run`、`/job` 不 publish，而且只在 `be` 網路：`/run` 會燒額度、寄信，只有 web 的維運頁（`/ops`，只有區網入口開得到）連得到，nginx、cloudflared 都碰不到。`cloudflared` 用 Tunnel token 執行，
  不需要 `cert.pem`、憑證檔或 `config.yml`；對外的主機名稱在 Cloudflare 後台設定。
- **web 不做任何身分驗證**（讀寫、`/auth` 都是），誰能連進來全由前面決定，所以 web 不 publish、只在 `app` 網路，只有 nginx 連得到；
  cloudflared 在另一個 `edge` 網路，碰不到 web。不要為了方便 publish web 的 8787，那等於整站（含 `/auth`）不設防。
- **nginx（[docker/nginx.conf](../docker/nginx.conf)）是唯一的閘門**，兩個入口：
  - `8080`：只給 cloudflared。身分由 Cloudflare Access 驗（Tunnel 也要開「驗 Access JWT」，步驟 2）；`/auth`、`/ops` 與 `/api/auth*`、`/api/ops*` 一律 404，`/api/session` 回 `{"local": false}`（不顯示授權與維運頁）。
  - `8788`：publish 到 host 的 IPv4（`0.0.0.0:8788`），給家裡區網／VPN 直接連，`/auth` 在這裡。只放行私有位址的來源（`geo $lan`），
    `Host` 必須是 IP 或 localhost（擋 DNS rebinding），有 `Origin` 的話也要是同一類位址。VPN 分到的網段不在私有位址裡（例如 Tailscale 的 `100.64.0.0/10`）就加進 `geo $lan`。
    路由器不要轉發 8788；只綁 IPv4 是因為 IPv6 經 docker-proxy 進來時來源會變成 bridge 閘道（私有位址），繞過來源檢查。
- 機密放 `.env`（Gmail 帳密、`TUNNEL_TOKEN`）：`web` 容器拿不到這些，`worker` 拿不到 `TUNNEL_TOKEN`。Claude 的 OAuth token 另外存在 volume 的 `state/oauth_token.json`（權限 600），`web` 負責驗證並儲存、`worker` 讀取。

`docker-autodeploy.sh` 與 `newsletter-autodeploy.{service,timer}` 是自動部署、`push-images.sh` 是 Actions 不能跑時手動推映像（步驟 6）；其他檔案是**不用 Docker 時**的範本：systemd 單元、cloudflared 設定、env 範本；`check_boundaries.py` 是模組依賴方向檢查（CI 也跑），與部署無關。

用 Podman 代替 Docker 也行（Debian 13 實測）：`apt install podman-docker docker-compose catatonit nftables aardvark-dns`、`systemctl enable --now podman.socket podman-restart.service`，
之後照常用 `docker compose`。`catatonit` 給 `init: true` 用，`nftables`、`aardvark-dns` 給 netavark 建網路與容器內 DNS 用。

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
2. 該 Tunnel 的 Public Hostname：填你的網域 `news.example.com`，Service 類型選 `HTTP`、URL 填 **`nginx:8080`**（compose 服務名稱；不是 web）。DNS 紀錄會自動建立。
3. Access → Applications → Add an application → Self-hosted：Application domain 填同一個 `news.example.com`；
   Policy 一條就好：Action = Allow，Include = Emails，只填你自己的 email。不要留 Everyone 之類的其他 policy。
4. （強烈建議，縱深防禦）同一個 Application 的 Overview 有 **Application Audience (AUD) Tag**；team 名稱是登入頁網址 `https://<team>.cloudflareaccess.com` 的 `<team>`。
   回到 Tunnel 的 Public Hostname → Additional application settings → Access，打開「Protect with Access／Enforce Access JWT validation」，填 team 與 AUD tag。
   cloudflared 就會驗每個請求帶的 Access JWT，沒有或無效就不轉給 nginx；之後 Access 政策就算被誤刪或改錯，也不會整站公開。
   web 自己不再驗，這是 Access 政策之外唯一的一道。

**3. 啟動**

```bash
docker compose up -d --build
docker compose ps        # web、nginx 要是 healthy、cloudflared 是 Up；PORTS 欄只有 nginx 的 0.0.0.0:8788->8788
```

`.env` 缺 `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`TUNNEL_TOKEN` 任何一個，`docker compose` 會直接報錯並指出缺哪個。

**貼上 OAuth token**（第一次、以及一年後 `newsletter-write` exit 5 或網頁頂端出現「Claude 授權已失效」時）：在**自己的電腦**執行 `claude setup-token`、
在瀏覽器完成授權，複製它印出的 token（只會印一次；需要 Pro／Max／Team／Enterprise），再用**家裡區網或 VPN 上的瀏覽器**開 `http://<host 的 IP>:8788/auth`
（要用 IP，`raspi.lan` 這種名字會被 nginx 拒絕），貼上、按「驗證並儲存」。不在家又沒有 VPN：`ssh -L 8788:127.0.0.1:8788 <host>`，再開 <http://localhost:8788/auth>。
從 Tunnel 網域開不了 `/auth`；那裡頁面頂端的橫幅只會寫「請在主機上重新授權」。
伺服器會先實際呼叫一次 Claude 驗證（用掉極少的訂閱額度），通過才存進 volume；舊的 token 不會被無效的覆蓋。
不用重啟、不用重新部署，worker 下一次執行就讀到新的（今天已經因此失敗的話，要手動補跑，見步驟 5）。沒做這一步的話，每天的排程會以 exit 2 結束（沒有 OAuth token）。

**4. 驗收**

| 檢查 | 預期 |
| --- | --- |
| `docker compose ps` | `web`、`worker`、`nginx` healthy、`cloudflared` Up、PORTS 欄只有 nginx 的 `0.0.0.0:8788->8788/tcp`（web 沒有） |
| 區網另一台 `curl -s -o /dev/null -w '%{http_code}\n' http://<host 的 IP>:8788/auth` | `200`（貼 token 頁） |
| 同上但 `-H 'Host: evil.example'` | `421`（Host 不是 IP 就拒絕） |
| 登入 Access 後開 `https://news.example.com/auth` | 「找不到頁面」（404），不是貼 token 的頁面 |
| `docker compose logs cloudflared` | 出現 `Registered tunnel connection`（沒有就是 token 有誤或出站連不到 Cloudflare） |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報（還沒有報告時是「還沒產出」頁） |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `newsletter-web`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | `docker compose exec web tail -n1 state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照 [apps/web-ui/README.md](../apps/web-ui/README.md)「PWA」一節的「正式站驗收」表 |
| `.env` 設了 `NEWSLETTER_BASE_URL` 後寄一封信，在手機點信裡的「👍 有用」連結 | 先經 Access 登入，再看到「確認標為 有用」頁；**這時 `feedback.jsonl` 還沒變**，按了確認才多一行 |
| `docker network inspect newsletter-ops_be --format '{{range .Containers}}{{.Name}} {{end}}'` | 只有 worker 與 web 的容器 |
| `docker compose run --rm worker newsletter-llm --auth-check` | 只驗證 OAuth token（一次最小的呼叫，不跑晨報），通過才表示每天的排程跑得起來 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，先手動跑一次（見下，會呼叫 Claude、用掉訂閱額度）。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。

**外部監控（建議）**：失敗通知是 worker 自己寄的，整台掛掉時不會有信。到 [healthchecks.io](https://healthchecks.io) 開一個 check
（週期 1 天、寬限 1～2 小時），把 ping 網址寫進 `.env` 的 `NEWSLETTER_HEARTBEAT_URL`，`docker compose up -d` 生效。說明見 [worker/README.md](../apps/worker/README.md)「heartbeat」。

**5. 日常操作**

從區網開 `http://<host 的 IP>:8788/ops`（頁首「維運」）就能做大部分的事：看排程與上次結果、手動執行（可選從哪一步開始）、試抓來源、重建知識圖譜，
以及最近幾天的耗時與用量、目前的來源與興趣設定（唯讀）。按下去是 web 直接呼叫 worker 的 `/run`、`/job`（見 [worker/README.md](../apps/worker/README.md)）。
經 Tunnel 從外面連看不到這一頁。指令列的做法：

```bash
docker compose logs -f worker                  # 排程輸出：下次執行時間、開始、結束；失敗時附上 logs/ 的最後 20 行
docker compose exec worker python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8790/status').read().decode())"   # 是否在跑、下次幾點、上次結果
docker compose exec worker python -c "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8790/run', method='POST')).read().decode())"   # 立刻跑一次（記成 manual；執行中回 409）。網址加 ?from=write／render 就從那一步開始，例如寄信設定修好後用 ?from=render，不必重寫報告
docker compose exec web ls reports                # volume 裡的報告
docker compose exec worker newsletter-metrics summary 14   # 最近 14 天每次執行的耗時、token、成本、結果（config 的 debug 要開）
docker compose run --rm -e NEWSLETTER_DEBUG=1 -e NEWSLETTER_RUN_LABEL=test worker newsletter-write   # 只產報告、不寄信（要先有當日 curated JSON）
git pull && docker compose up -d --build          # 手動更新（interests.md、config/、程式都在映像裡，要重 build）
```

- 每天 08:00（`config/config.json` 的時區，`.env` 的 `NEWSLETTER_RUN_AT` 可改）。容器啟動時，今天過了時間卻還沒跑過（或上次跑到一半被停掉）就補跑一次；
  只補同一天，跨日就算了。今天跑過但失敗了，30 分鐘後從失敗的那一步自動重跑**一次**（網路、來源暫時掛掉；寄信失敗不會重寫報告）；認證失效或額度用完不重跑（避免每次都燒一次），修好後用上面的 `/run` 補。
  手動跑的也算「今天跑過」，所以 08:00 前手動跑完，08:00 就不會再寄一次。`docker stop` 會等執行中的步驟收尾（最多 2 分鐘）。
- 每天 07:30（`NEWSLETTER_AUTH_CHECK_AT`）worker 驗一次 Claude 認證（一次極小的呼叫），結果寫 `state/auth_health.json`，網頁的授權橫幅讀它。
- `up -d --build` 只會重建有變動的容器；`web` 當掉或被重建時，`cloudflared` 靠服務名稱 `web` 重新連上，不必另外處理。
- 三個容器都以唯讀根目錄、`cap_drop: [ALL]`、`no-new-privileges` 執行；web／worker 只有 volume、`/tmp`、`/home/app`（tmpfs，claude CLI 的設定與 session 紀錄，重啟就清掉）可寫。要在容器裡臨時寫檔請寫 `/tmp`。
- `cloudflared` 映像固定版本（compose 裡的 tag），timer 不會換它；要升級就改 tag、發行，看 `docker compose logs cloudflared` 有沒有 `Registered tunnel connection`。

**6. 自動部署（GHCR）**

`main` 每次更新、`selftest` 通過後，[release.yml](../.github/workflows/release.yml) 會建兩個映像推到
`ghcr.io/ponywen0819/newsletter-ops-web` 與 `ghcr.io/ponywen0819/newsletter-ops-worker`，tag 是 `latest` 與 `sha-<commit>`，只建 `linux/arm64`。
兩個 workflow 都跑在自架 runner（標籤 `self-hosted, linux, x64, docker`，Windows 的 WSL2），不用 GitHub 的分鐘數；x64 上建 arm64 映像靠 QEMU，比較慢。
runner 關機時工作會排隊等它。repo 是公開的，fork 來的 PR 不會在 runner 上跑。
發行時在 `main` 打的 `v*` tag（例如 `git tag v1.2.0 && git push origin v1.2.0`）也會建一次，映像 tag 是同名的 `v1.2.0` 與 `sha-<commit>`。
host 上的 timer 每 10 分鐘跑一次：先 `git pull --ff-only`（compose 檔跟著 repo），再拉映像，有新版才重建容器。repo 要停在 `main`。

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
- **退版**：`.env` 加 `NEWSLETTER_TAG=v1.1.0`（或 `sha-<commit>`），然後 `docker compose up -d`；固定 tag 時 timer 只會拉同一個 tag，等於停止自動更新。要回到自動跟新版就把那行刪掉。
- host 上的 repo 有本機修改或分岔時 `git pull` 會失敗，這輪整個不更新（看 journal）；本機設定放 `.env` 或 `docker-compose.override.yml`（未追蹤的檔不影響）。
- podman 的 `docker compose ps` 狀態欄不會顯示 healthy，要看健康狀態用 `docker inspect newsletter-ops-worker-1 --format '{{.State.Health.Status}}'`。
- **從單一映像換過來時**（舊的 `ghcr.io/ponywen0819/newsletter-ops`）：先 `git pull` 拿到新的 compose，再 `docker compose up -d`；
  確認兩個容器都 healthy 後，`docker image rm ghcr.io/ponywen0819/newsletter-ops:latest` 刪掉舊映像（不會自己刪）。

Actions 不能跑時（例如額度用完）可以在自己的電腦建好推上去，結果和 release.yml 一樣。需要 `write:packages` 的 classic PAT：

```bash
echo <PAT> | docker login ghcr.io -u Ponywen0819 --password-stdin
deploy/push-images.sh          # origin/main → :latest 與 :sha-<commit>
deploy/push-images.sh v1.2.0   # 版本 tag → :v1.2.0 與 :sha-<commit>
```

一律從 `git archive` 的乾淨副本建，工作目錄有沒有修改都不影響。

**7. 資料保存：只備份回饋**

生成物都在 volume `newsletter-data`（容器內 `/var/lib/newsletter`），換容器、換映像、`docker compose down` 都還在；
**`docker compose down -v` 或 `docker volume rm` 才會刪掉**。只有 `state/feedback.jsonl` 每週日寄一份到 `NEWSLETTER_MAIL_TO`，決定見[根目錄 README](../README.md)「資料保存」。

**8. Tunnel 設定進版控（tunnel-sync）**

正式區 Tunnel 的 Public Hostname 以 `deploy/cloudflare-tunnel.json` 為準，由 [tunnel-sync.yml](../.github/workflows/tunnel-sync.yml) 同步：
PR 到 `develop`／`main` 時驗檔案、與線上比對（差異寫在 Actions 的 Summary），`main` 更新後才 PUT 整份設定並補上缺的 DNS CNAME。
**導入後就只改這個檔，不要在網頁上改 Tunnel 路由**：下次套用會整份蓋掉。Access Application 不在同步範圍，新增 hostname 時仍要手動建。

`check` 會擋下：沒有 catch-all、hostname 重複，以及**沒開 Enforce Access JWT validation**（`originRequest.access.required`）的 hostname，
也就是步驟 2 第 4 點從「強烈建議」變成必做。

導入（一次）：

1. Cloudflare → My Profile → API Tokens → Custom token，權限 `Account › Cloudflare Tunnel › Edit` 與 `Zone › DNS › Edit`，範圍只選自己的帳號與網域。
2. repo 的 Actions secrets：`CF_API_TOKEN`、`CF_ACCOUNT_ID`、`CF_ZONE_ID`（網域 Overview 右下）、`CF_TUNNEL_ID`（Tunnel 頁面的 UUID）。
3. 匯出線上設定，commit 到開 PR 的分支：

```bash
read -rs CF_API_TOKEN && export CF_API_TOKEN CF_ACCOUNT_ID=<...> CF_TUNNEL_ID=<...>
deploy/tunnel-sync.sh export   # 寫出 deploy/cloudflare-tunnel.json
deploy/tunnel-sync.sh check    # 不連網；要過才合得進去
deploy/tunnel-sync.sh diff     # 與線上比對
```

   還沒改成 nginx 架構的話，匯出的檔是舊的（`http://web:8787`、沒開 Access JWT），`check` 不會過。直接在檔裡改好再 commit：
   每個 hostname 的 `service` 改成 `http://nginx:8080`，並加上
   `"originRequest": {"access": {"required": true, "teamName": "<team>", "audTag": ["<AUD tag>"]}}`。
   PR 的 Diff 會列出這些改動，合進 `main` 後才套用（同時要發行新版，正式區才有 nginx 容器）。

workflow 和 selftest 一樣跑在自架 runner（`self-hosted, linux, x64`），不用 GitHub 的分鐘數；fork 來的 PR 不在 runner 上跑。runner 沒有 jq 時會下載固定版本到暫存目錄。

## 測試區（區域網路，沒有 Tunnel）

[`docker-compose.test.yml`](../docker-compose.test.yml) 是另一組 web＋worker，給區域網路直接連，用來測試、或讓本機開發的前端接一個已經跑著的後端，不必整套重建。
建議放在正式區以外的機器（例如 Windows 的 Podman），弄壞了也不影響正式區。

| | 正式區 | 測試區 |
|---|---|---|
| 對外 | Tunnel（Access）經 nginx:8080；區網／VPN 經 nginx:8788 | 18787（web 直接開，含 `/auth`）、18790（worker）對區網開放，沒有 nginx、沒有登入 |
| `/auth` | `http://<host 的 IP>:8788/auth`（nginx 只放行私有來源、IP 形式的 Host） | `http://<IP>:18787/auth`（誰都能開） |
| 排程 | 每天跑、補跑、驗認證、寄提醒與備份 | 全關（`NEWSLETTER_SCHEDULE=off`），只有 `POST /run` |
| 資料 | volume `newsletter-ops_newsletter-data` | volume `newsletter-ops-test_newsletter-data`（專案名不同，互不相干） |
| metrics | `prod` | `test`（`NEWSLETTER_RUN_LABEL=test`） |
| 映像 | GHCR | 在那台機器上建（`localhost/…:test`） |

區網上誰都能看報告、按回饋、觸發 `/run`（會用到 Claude 額度），也能換掉或刪除測試區的 token，所以只在信得過的家用網路開，路由器不要轉發這些 port；有公網 IPv6 的話用防火牆擋掉。

### Preview：正式區的設定跑在測試機

要驗證 nginx、Tunnel 這些只有正式區才有的東西，在測試機上直接跑 `docker-compose.yml`（不是 test 版），例如 Dokploy 另開一個 Compose 專案指向 `docker-compose.yml`：

- 另建一個 Tunnel 與 Access Application（例如 `news-preview.example.com`），用它的 `TUNNEL_TOKEN`，Service 一樣填 `nginx:8080`。**不要用正式區的 token**：同一個 Tunnel 的連線會被兩邊分走。
- `.env` 的 Gmail 可以填自己，或先不跑排程：設 `NEWSLETTER_RUN_AT` 到一個不會碰到的時間，要跑時 `docker compose exec worker …`／`POST /run`（worker 不 publish，只能從容器內打）。
- 8788 會開在測試機上；和正式區的 8788 不同台，不衝突。測試區（18787、18790）可以同時開著。

### Windows（Podman）

PowerShell：

```powershell
winget install RedHat.Podman Docker.DockerCompose     # podman compose 會用 docker-compose
podman machine init; podman machine start
git clone https://github.com/Ponywen0819/newsletter-ops.git; cd newsletter-ops
podman compose -f docker-compose.test.yml up -d --build
# 只對「私人」網路開
New-NetFirewallRule -DisplayName "newsletter-ops test" -Direction Inbound -Protocol TCP -LocalPort 18787,18790 -Action Allow -Profile Private
```

- repo 的 `.gitattributes` 讓 `*.sh` 保持 LF；clone 之前就改過 `core.autocrlf` 也沒關係。
- token：在這台機器上用 `claude setup-token` 另外產生一個，在區網任一台開 `http://<這台的 IP>:18787/auth` 貼上。和正式區用不同的 token，這邊被換掉或撤銷都不影響正式區（額度是同一個訂閱）。
- 要寄信就在 repo 根放 `.env`，設 `GMAIL_USER`、`GMAIL_APP_PASSWORD`（`NEWSLETTER_MAIL_TO` 預設寄給自己）；沒設的話流程停在寄信那步（exit 2），報告照樣寫好。
- 跑一次：`curl.exe -X POST http://<這台的 IP>:18790/run`，或 `?from=write` 只重寫報告；`GET /status` 看進度。
- 更新：`git pull` 後再跑一次 `up -d --build`。區網連不到時，先在這台用 `localhost:18787` 試，再查防火牆與網路設定檔是不是「私人」。

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

- **web 不做身分驗證**：cloudflared 直接接 `127.0.0.1:8787` 時，`/auth` 要靠 cloudflared 的 ingress 擋（[cloudflared-config.yml.example](cloudflared-config.yml.example) 已寫好，`path` 那條不能刪），Access JWT 也由它驗。
- 貼 Claude 的 OAuth token：systemd 部署沒有 nginx，`/auth` 就在主 port，在 host 上開 `http://127.0.0.1:8787/auth`（遠端 host 用 `ssh -L 8787:127.0.0.1:8787 <host>` 後開 `http://localhost:8787/auth`），做法見 [llm/README.md](../packages/llm/README.md)「認證」。沒貼的話排程會以 exit 2 結束。
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
