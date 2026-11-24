# 部署到家用 host（Docker）

目標：worker 與 web 跑在家裡一台 host（VM 也行，只要有 Docker），經入口閘門在外也能看晨報、按有用／沒用。測試區是另一台機器上的另一組容器，見[測試區](#測試區區域網路沒有入口閘門)。
host 上不用裝 Python、uv，這個 repo 也不 publish 任何 port。專案全貌見[根目錄 README](../README.md)。

**這個 repo 只負責服務本身（web、worker）。** 對外入口——Cloudflare Tunnel、Access、nginx、Tunnel 路由與 DNS——在 **service-core** repo 的 `gateway/`，
由那邊部署與維護（它的 README「接上 gateway」「newsletter-ops 改接 gateway」）。這邊只規定閘門必須做到什麼，見[入口閘門的約定](#入口閘門的約定)。

```
手機／筆電 ──https──▶ Cloudflare（Access）──Tunnel──▶ ┐
家裡區網／VPN ──────────────▶ host:8788 ─────────────▶ ┤  service-core 的 gateway（cloudflared + nginx）
                                                       ▼
                         gw-newsletter 網路（gateway 建立）── web 容器（別名 newsletter-web:8787，不 publish）
worker 容器：每天 08:00 跑每日流程（be 網路，只有 web 連得到）；web 與 worker 共用一個 volume：newsletter-data
```

- `Dockerfile`：一個檔、兩個 target，建出兩個映像：`newsletter-ops-web`（`--target web`）與 `newsletter-ops-worker`（`--target worker`）。
  各自只帶自己 app 的 venv（`/opt/web-server`、`/opt/worker`，用 uv 鎖定的依賴、不可編輯安裝），**不帶原始碼、也沒有 uv**，指令直接在 PATH 上；
  非 root 執行，兩邊共用 base 層（使用者、`config/`、volume 連結）。web 另外帶前端：先用 Node 建置 `apps/web-ui/`，只把 `dist` 帶進映像，
  所以 host 與映像裡都不需要 Node；worker 另外帶 `.claude/`（寫報告的 skill）。`.dockerignore` 是白名單，前端原始碼與 `apps/web-ui/public/`（圖示）要放行才進得了 build context。
- `docker-compose.yml`：`web`、`worker` 兩個服務與 volume。`worker` 跑 `worker`（見 [apps/worker/README.md](../apps/worker/README.md)），
  它的 `/status`、`/run`、`/job` 不 publish，而且只在 `be` 網路：`/run` 會燒額度、寄信，只有 web 的維運頁（`/ops`，閘門只在區網入口放行）連得到，閘門碰不到 worker。
- **web 不做任何身分驗證**（讀寫、`/auth` 都是），誰能連進來全由閘門決定，所以 web 不 publish，只加入 gateway 建立的 `gw-newsletter` 網路（`external: true`，別名 `newsletter-web`）與 `be`。
  不要為了方便 publish web 的 8787，那等於整站（含 `/auth`）不設防。`gw-newsletter` 不存在時 `docker compose up` 會直接失敗：**gateway 要先部署**。
- 機密放 `.env`（Gmail 帳密），只有 `worker` 拿得到，`web` 容器拿不到。`TUNNEL_TOKEN` 屬於 gateway，不放這裡。Claude 的 OAuth token 另外存在 volume 的 `state/oauth_token.json`（權限 600），`web` 負責驗證並儲存、`worker` 讀取。

`deploy.sh` 是部署（CI 經 ssh 呼叫，也能在 host 上手動跑）、`push-images.sh` 是 Actions 不能跑時手動推映像（步驟 6）；其他檔案是**不用 Docker 時**的範本：systemd 單元、env 範本；`check_boundaries.py` 是模組依賴方向檢查（CI 也跑），與部署無關。

### 入口閘門的約定

web 本身沒有登入，安全全靠前面的閘門。閘門的實作在 service-core（`gateway/nginx/conf.d/newsletter.conf`、`gateway/cloudflare-tunnel.json`），
但**它必須做到的事由這個服務決定**，改 web 的路由或安全假設時要同步回去。web 的哪些行為依賴這些見 [web-server/README.md](../apps/web-server/README.md)「安全邊界」。

| 入口 | 閘門必須做到 |
| --- | --- |
| 對外（Tunnel） | 身分由 Cloudflare Access 驗，Tunnel 也要開 Enforce Access JWT validation（Access 政策被誤刪時不會整站公開）。<br>`/auth`、`/ops`（含子路徑）改寫成 `/not-found` 再轉給 web（前端顯示「找不到」）；`/api/auth*`、`/api/ops*` 直接回 404 `{"error": "not found"}`；`/api/session` 回 `{"local": false}`（不顯示授權與維運頁）。其餘照轉。 |
| 區網／VPN（host 的 8788） | 只放行私有位址的來源，`Host` 必須是 IP 或 localhost（擋 DNS rebinding，否則 421），有 `Origin` 的話也要是同一類位址（否則 403）。`/auth`、`/ops` 在這裡。<br>容器網路的位址不能算區網（別的服務會冒充）；只綁 IPv4（IPv6 經 docker-proxy 來源會變成 bridge 閘道）。<br>`proxy_read_timeout` 至少 150 秒：貼 token 時 web 會等 Claude 驗證最多 90 秒。 |
| 兩者 | 只有 web 進 `gw-newsletter`；worker 永遠不對閘門開放。路徑比對要擋得住 `//api/auth`、`/api/./ops` 這類寫法。 |

新增會寫入或會觸發 worker 的路由時，先想清楚它該不該在 Tunnel 那一側擋掉；要擋的話 service-core 的站台設定也要改。

用 Podman 代替 Docker 也行（Debian 13 實測）：`apt install podman-docker docker-compose catatonit nftables aardvark-dns`、`systemctl enable --now podman.socket podman-restart.service`，
之後照常用 `docker compose`。`catatonit` 給 `init: true` 用，`nftables`、`aardvark-dns` 給 netavark 建網路與容器內 DNS 用。

**1. 取得程式與機密**

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops && cd ~/newsletter-ops
cp .env.example .env && chmod 600 .env
$EDITOR .env      # GMAIL_USER、GMAIL_APP_PASSWORD
```

Claude 的 OAuth token（`claude setup-token` 取得，效期一年，只走訂閱額度，見 [llm/README.md](../packages/llm/README.md)「認證」）**不放 `.env`、也不讀環境變數**：
放環境變數的話，一年後換新就得改檔並重新部署。它存在 volume 的 `state/oauth_token.json`，換新立刻生效，做法在步驟 3 之後的「貼上 OAuth token」。

**2. 入口閘門（在 service-core）**

Tunnel、Access、nginx 都由 service-core 的 `gateway/` 部署（它的 README「第一次設定」）。這邊只要確認：

- gateway 已經部署、`gw-newsletter` 網路存在（`docker network ls | grep gw-newsletter`）；
- 它的 newsletter 站台設定符合上面的[入口閘門的約定](#入口閘門的約定)。

**3. 啟動**

```bash
docker compose up -d --build
docker compose ps        # web、worker 要是 healthy；PORTS 欄是空的（8788 在 gateway 的 nginx 上）
```

`.env` 缺 `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`NEWSLETTER_DATABASE_URL` 任何一個，`docker compose` 會直接報錯並指出缺哪個；`gw-newsletter` 不存在時也會報錯（先部署 gateway）。

**貼上 OAuth token**（第一次、以及一年後 `newsletter-write` exit 5 或網頁頂端出現「Claude 授權已失效」時）：在**自己的電腦**執行 `claude setup-token`、
在瀏覽器完成授權，複製它印出的 token（只會印一次；需要 Pro／Max／Team／Enterprise），再用**家裡區網或 VPN 上的瀏覽器**開 `http://<host 的 IP>:8788/auth`
（8788 是 gateway 的區網入口；要用 IP，`raspi.lan` 這種名字會被拒絕），貼上、按「驗證並儲存」。不在家又沒有 VPN：`ssh -L 8788:127.0.0.1:8788 <host>`，再開 <http://localhost:8788/auth>。
從 Tunnel 網域開不了 `/auth`；那裡頁面頂端的橫幅只會寫「請在主機上重新授權」。
伺服器會先實際呼叫一次 Claude 驗證（用掉極少的訂閱額度），通過才存進 volume；舊的 token 不會被無效的覆蓋。
不用重啟、不用重新部署，worker 下一次執行就讀到新的（今天已經因此失敗的話，要手動補跑，見步驟 5）。沒做這一步的話，每天的排程會以 exit 2 結束（沒有 OAuth token）。

**4. 驗收**

閘門是別的 repo 部署的，但它有沒有守住約定只能從這個服務的角度驗，所以下表照樣每次切換或改 gateway 後都要跑一遍。

| 檢查 | 預期 |
| --- | --- |
| `docker compose ps` | `web`、`worker` healthy，PORTS 欄是空的（web、worker 都不 publish） |
| `docker network inspect gw-newsletter --format '{{range .Containers}}{{.Name}} {{end}}'` | 只有 gateway 的 nginx 與這邊的 web，沒有 worker |
| 區網另一台 `curl -s -o /dev/null -w '%{http_code}\n' http://<host 的 IP>:8788/auth` | `200`（貼 token 頁） |
| 同上但 `-H 'Host: evil.example'` | `421`（Host 不是 IP 就拒絕） |
| 登入 Access 後開 `https://news.example.com/auth`、`/ops` | 「找不到頁面」（404），不是貼 token 或維運的頁面 |
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
deploy/deploy.sh v1.4.0                           # 手動部署某個版本（平常由 CI 做，見步驟 6）
```

- 每天 08:00（`config/config.json` 的時區，`.env` 的 `NEWSLETTER_RUN_AT` 可改）。容器啟動時，今天過了時間卻還沒跑過（或上次跑到一半被停掉）就補跑一次；
  只補同一天，跨日就算了。今天跑過但失敗了，30 分鐘後從失敗的那一步自動重跑**一次**（網路、來源暫時掛掉；寄信失敗不會重寫報告）；認證失效或額度用完不重跑（避免每次都燒一次），修好後用上面的 `/run` 補。
  手動跑的也算「今天跑過」，所以 08:00 前手動跑完，08:00 就不會再寄一次。`docker stop` 會等執行中的步驟收尾（最多 2 分鐘）。
- 白天 13:00、18:00、23:00（`NEWSLETTER_ROUNDS`）各跑一輪 round：候選池與知識圖譜，會用 Haiku 的訂閱額度；錯過超過兩小時的那輪不補。
- 每天 07:30（`NEWSLETTER_AUTH_CHECK_AT`）worker 驗一次 Claude 認證（一次極小的呼叫），結果寫 `state/auth_health.json`，網頁的授權橫幅讀它。
- `up -d --build` 只會重建有變動的容器；`web` 當掉或被重建時，gateway 靠 `gw-newsletter` 上的別名 `newsletter-web` 重新連上，不必另外處理（期間那個站台回 502）。
- 兩個容器都以唯讀根目錄、`cap_drop: [ALL]`、`no-new-privileges` 執行，只有 volume、`/tmp`、`/home/app`（tmpfs，claude CLI 的設定與 session 紀錄，重啟就清掉）可寫。要在容器裡臨時寫檔請寫 `/tmp`。

**6. 發行與部署（CI 推送，正式機不輪詢）**

發行時在 `main` 打 `v*` tag（例如 `git tag v1.2.0 && git push origin v1.2.0`），[release.yml](../.github/workflows/release.yml) 會建一次兩個映像推到
`ghcr.io/ponywen0819/newsletter-ops-web` 與 `ghcr.io/ponywen0819/newsletter-ops-worker`，同一個映像打上 `v1.2.0`、`latest`、`sha-<commit>`，只建 `linux/arm64`。
兩個都推上去後，接著跑 [deploy.yml](../.github/workflows/deploy.yml)：ssh 到正式機執行 [`deploy.sh`](deploy.sh) `v1.2.0`——
repo 切到那個 tag（compose 檔跟著版本走）、拉同一個版本的映像、`up -d --remove-orphans`、等 web 與 worker healthy，結果就是 Actions 的成敗。
每日流程、白天那輪或手動工作正在跑時，`deploy.sh` 會等它跑完（最多 30 分鐘）再重建，不打斷執行。

`selftest` 只在 PR 跑，合併到 `main` 與打 tag 都不再重跑；**只合併不打 tag 不會出新映像，也不會部署**——正式機只在打 tag 時變動。
workflow 都跑在自架 runner（標籤 `self-hosted, linux, x64`，Windows 的 WSL2，和正式機同一個區網），不用 GitHub 的分鐘數；x64 上建 arm64 映像靠 QEMU，比較慢。
runner 關機時工作會排隊等它。repo 是公開的，fork 來的 PR 不會在 runner 上跑；deploy 只有 tag push 與手動觸發會跑。

設定（一次）：

1. 產一把只給部署用的 key（不要用自己的）：`ssh-keygen -t ed25519 -N '' -C newsletter-deploy -f newsletter-deploy`。
2. 正式機的 `/root/.ssh/authorized_keys` 加一行，用 `command=` 把這把 key 限定成只能跑 `deploy.sh`（dropbear 也支援這些選項）：
   ```
   command="/root/newsletter-ops/deploy/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA… newsletter-deploy
   ```
   CI 送過去的「指令」只是版本號，`deploy.sh` 從 `SSH_ORIGINAL_COMMAND` 讀它並檢查格式；拿到這把 key 的人頂多能部署某個已存在的 tag。
3. repo 設定 → Environments → `production`（第一次跑會自動建立）：
   - Variables：`DEPLOY_HOST` = `root@<正式機的區網 IP 或名稱>`（runner 解析得到的）
   - Secrets：`DEPLOY_SSH_KEY` = 私鑰檔的內容；`DEPLOY_KNOWN_HOSTS` = 在 runner 上 `ssh-keyscan <host>` 的輸出（固定 host key，不接受第一次連線就信任）
   - 想在部署前按一下核准的話，加 Required reviewers。
4. 正式機上的 repo 之後由 `deploy.sh` 切到 detached 的 tag，不必停在 `main`，也不要有本機修改（有的話 `deploy.sh` 拒絕部署）；本機設定放 `.env` 或 `docker-compose.override.yml`。
5. 舊的輪詢 timer 停掉並刪除（以前的版本才有）：
   ```bash
   systemctl --user disable --now newsletter-autodeploy.timer
   rm ~/.config/systemd/user/newsletter-autodeploy.{service,timer} && systemctl --user daemon-reload
   ```

日常：

- 部署紀錄就在 Actions 的 deploy job；重新部署或**退版**：Actions → deploy → Run workflow，填版本號（例如 `v1.4.0`）。
  `deploy.sh` 之前的版本（v1.3.0 以前）沒有這支腳本，退到那麼舊要在 host 上手動 `git checkout` 與 `NEWSLETTER_TAG=… docker compose up -d`。
- 也可以在 host 上直接跑 `deploy/deploy.sh v1.4.0`，效果一樣。
- `.env` 的 `NEWSLETTER_TAG` 由 `deploy.sh` 維護，記的是目前部署的版本，所以在 host 上手動下 `docker compose …` 也是同一版；不要手動改它，要換版本就部署。
- GHCR 的 package 是 private 時，正式機要先 `docker login ghcr.io`（PAT 只要 `read:packages`）。
- podman 的 `docker compose ps` 狀態欄不會顯示 healthy，要看健康狀態用 `docker inspect newsletter-ops-worker-1 --format '{{.State.Health.Status}}'`（`deploy.sh` 也是這樣等的）。

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
知識圖譜存在 Postgres（`NEWSLETTER_DATABASE_URL`），壞了或換資料庫可直接用 `newsletter-kg --rebuild` 從 `data/curated/` 重建。

**8. 從自帶入口的舊版切換**

舊版的 compose 自己帶 `nginx`、`cloudflared`，Tunnel 路由也由這個 repo 的 tunnel-sync 管；現在都搬到 service-core。
切換順序照 service-core README「newsletter-ops 改接 gateway」，重點：

1. 先在正式機停掉舊的入口：`docker compose stop nginx cloudflared`。舊 cloudflared 還在跑的話，同一條 Tunnel 收到新路由會找不到 `gateway-nginx`，一部分請求 502；舊 nginx 的 8788 也會和 gateway 撞。
2. 部署 gateway、套用 Tunnel 路由（service-core 那邊）。
3. 再部署這邊的新版（打 tag，或 `deploy/deploy.sh <版本>`），`--remove-orphans` 會刪掉舊的 nginx、cloudflared 容器。
   gateway 還沒部署的話，`up` 會因為找不到 `gw-newsletter` 失敗、deploy job 變紅，舊容器原封不動；部署好 gateway 後重跑 deploy 即可。
4. 跑一遍步驟 4 的驗收表。
5. 這個 repo 的 Actions secrets `CF_API_TOKEN`、`CF_ACCOUNT_ID`、`CF_ZONE_ID`、`CF_TUNNEL_ID` 已經沒有 workflow 在用，可以刪掉；`.env` 的 `TUNNEL_TOKEN` 也是（搬到 gateway 的 Environment）。

## 資料庫（Postgres）

知識圖譜（KG）存在 Postgres，由環境變數 `NEWSLETTER_DATABASE_URL` 指定。多個專案共用同一個 database（例如 `personal`）時，各專案建一個獨立的 schema：

```sql
CREATE SCHEMA IF NOT EXISTS newsletter;
```

連線字串用 `options` 帶 `search_path`（`=` 要 URL 編碼成 `%3D`）：

```bash
NEWSLETTER_DATABASE_URL="postgresql://<user>:<password>@<host>:5432/personal?options=-csearch_path%3Dnewsletter"
```

- **容器連 host 本機的 Postgres**：容器內不能用 `127.0.0.1`，要填 host 的區網 IP，或 `host.docker.internal`（Podman 用 `host.containers.internal`），並確認 Postgres 的 `listen_addresses` 與 `pg_hba.conf` 放行該來源。
- **測試區**：在測試區 `.env` 指向另一個 schema（例如 `newsletter_test`），避免和正式區互相覆蓋。
- **一次性搬遷（從舊版 `state/kg.sqlite` 升級）**：
  1. 確認 Postgres 已建好 schema，並在 `.env` 加上 `NEWSLETTER_DATABASE_URL`。
  2. 停掉 worker，避免遷移期間繼續寫入舊檔：`docker compose stop worker`。
  3. 部署新版（打 tag 讓 CI 部署，或 `deploy/deploy.sh <版本>`）。部署會把新版 worker 開起來。
  4. 馬上再停 worker，在它寫進 Postgres 之前匯入舊檔，然後開回來：
     `docker compose stop worker && docker compose run --rm worker newsletter-kg --import-sqlite /var/lib/newsletter/state/kg.sqlite && docker compose start worker`
     （`deploy.sh` 把部署的版本寫進了 `.env` 的 `NEWSLETTER_TAG`，所以 `run` 用的就是新版映像。）
  5. 開網頁確認脈絡圖（`/kg`）顯示正常。舊的 `state/kg.sqlite` 先留著不急著刪；若要回退，部署上一個 tag 即可。

## 測試區（區域網路，沒有入口閘門）

[`docker-compose.test.yml`](../docker-compose.test.yml) 是另一組 web＋worker，給區域網路直接連，用來測試、或讓本機開發的前端接一個已經跑著的後端，不必整套重建。
建議放在正式區以外的機器（例如 Windows 的 Podman），弄壞了也不影響正式區。

| | 正式區 | 測試區 |
|---|---|---|
| 對外 | 經 service-core 的 gateway：Tunnel（Access）與區網／VPN 的 8788 | 18787（web 直接開，含 `/auth`）、18790（worker）對區網開放，沒有閘門、沒有登入 |
| `/auth` | `http://<host 的 IP>:8788/auth`（閘門只放行私有來源、IP 形式的 Host） | `http://<IP>:18787/auth`（誰都能開） |
| 排程 | 每天跑、補跑、驗認證、寄提醒與備份 | 全關（`NEWSLETTER_SCHEDULE=off`），只有 `POST /run` |
| 資料 | volume `newsletter-ops_newsletter-data` | volume `newsletter-ops-test_newsletter-data`（專案名不同，互不相干） |
| metrics | `prod` | `test`（`NEWSLETTER_RUN_LABEL=test`） |
| 映像 | GHCR | 在那台機器上建（`localhost/…:test`） |

區網上誰都能看報告、按回饋、觸發 `/run`（會用到 Claude 額度），也能換掉或刪除測試區的 token，所以只在信得過的家用網路開，路由器不要轉發這些 port；有公網 IPv6 的話用防火牆擋掉。

### Preview：正式區的設定跑在測試機

要驗證正式區的 compose（不 publish、接 `gw-newsletter`）本身，在測試機上直接跑 `docker-compose.yml`（不是 test 版），例如 Dokploy 另開一個 Compose 專案指向 `docker-compose.yml`：

- 測試機上要有 `gw-newsletter` 網路：部署一份 service-core 的 gateway，或只驗容器、不需要入口時手動 `docker network create gw-newsletter`（這時從外面連不到 web）。
  要連 Tunnel 的話另建一個 Tunnel 與 Access Application（例如 `news-preview.example.com`），**不要用正式區的 token**：同一個 Tunnel 的連線會被兩邊分走。
- `.env` 的 Gmail 可以填自己，或先不跑排程：設 `NEWSLETTER_RUN_AT` 到一個不會碰到的時間，要跑時 `docker compose exec worker …`／`POST /run`（worker 不 publish，只能從容器內打）。
- 測試區（18787、18790）可以同時開著。

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
- 信預設不寄：寫成 volume 的 `logs/outbox/*.eml`（`NEWSLETTER_MAIL_OUTBOX=logs/outbox`），用 `npm run devtools -- outbox` 取回來看。
  要真的寄，在 repo 根的 `.env` 設 `GMAIL_USER`、`GMAIL_APP_PASSWORD`（`NEWSLETTER_MAIL_TO` 預設寄給自己），並加一行 `NEWSLETTER_MAIL_OUTBOX=`（空的）。
- 跑一次：`curl.exe -X POST http://<這台的 IP>:18790/run`，或 `?from=write` 只重寫報告；`GET /status` 看進度。
- 更新：`git pull` 後再跑一次 `up -d --build`。區網連不到時，先在這台用 `localhost:18787` 試，再查防火牆與網路設定檔是不是「私人」。

## 開發工具（devtools）

[`deploy/devtools.py`](devtools.py) 把正式區、測試區、本機三邊串起來。在 repo 根執行 `npm run devtools -- <指令>`（等於 `uv run --project packages/shared python deploy/devtools.py`），`--help` 看全部參數。

| 指令 | 做什麼 |
|---|---|
| `snapshot` | 正式區的報告、curated、知識圖譜、回饋複製到測試區（`--to-local` 改成本機）；快照留在 `.devtools/snapshots/`，`--from <檔>` 可以重複匯入 |
| `compare <日期>` | 測試區用正式區那天的 curated 重寫報告，和正式區那份比主旨、頭條、各段收了哪幾則；逐行比較存成 `.devtools/compare/<日期>.html`。改 skill、提示或篩選後先在這裡看差異。會用 Claude 額度；`--no-run` 只比較不重寫 |
| `outbox` | 測試區「寄出」的信（見上面 Windows 一節）存成 `.devtools/outbox/*.html`，瀏覽器直接開 |
| `seed` | 本機開發用的三天範例資料（[`sample-data/`](sample-data)，虛構內容），日期平移到今天，接著 `npm run dev` 就有東西看；也可以 `npm run seed` |
| `status` | 兩區的 worker 狀態、最近一次執行、token 剩幾天、報告與回饋數 |
| `restore-feedback <檔>` | 回饋備份信的附件還原到測試區（或 `--to-local`）：先檢查每一列、和最新快照比少了幾則，寫入後再比對內容。定期做一次，確認備份真的能用 |

設定放在 repo 根的 `.env`（compose 不會讀這幾個）或環境變數：

```bash
NEWSLETTER_PROD_SSH=me@home-host      # 正式機；用 ~/.ssh/config 的別名也行。snapshot、status 會用到
# NEWSLETTER_TEST_SSH=me@test-host    # 測試區不在跑 devtools 的這台時才設
# NEWSLETTER_PROD_RUNTIME=docker      NEWSLETTER_TEST_RUNTIME=podman（預設：這台有 podman 就用）
# NEWSLETTER_PROD_PROJECT=newsletter-ops   NEWSLETTER_TEST_PROJECT=newsletter-ops-test
```

- **怎麼連**：用 ssh 在正式機上 `docker exec` 進 worker 容器，跑 devtools 送進去的一小段 Python（`python -c`），所以正式區不用先部署新版。
  正式機的帳號要能執行 docker（在 `docker` 群組），ssh 要能免密碼登入（金鑰或 agent）。
- **對正式區只讀**：打包時拿 pipeline 鎖（每日流程在跑就 exit 75，等它跑完再試），回饋、知識圖譜也各自拿鎖；知識圖譜以唯讀連線從 Postgres 匯出成快照裡的 `state/kg.json`，匯入端載入目標 Postgres（目標要設 `NEWSLETTER_DATABASE_URL`，沒設會略過 KG 並提醒；舊版含 `state/kg.sqlite` 的快照匯入時會略過該檔並提示改用 `newsletter-kg --import-sqlite`）。
- **不搬的**：`oauth_token.json`（兩區的 token 本來就分開）、`auth_health.json`、`last_job.json`、`logs/`、`data/raw/`。
  測試區的這些目錄（`reports/`、`data/curated|kg|search/`）會變成和正式區一模一樣：測試區自己多出來的報告會被刪掉。
- 快照、比較結果、取回的信都在 `.devtools/`，不進版控；快照裡有真實的回饋，不要外流。
- `compare` 經 worker 的 `POST /job?kind=rewrite&date=YYYY-MM-DD` 重寫，這個工作只有測試區（`NEWSLETTER_SCHEDULE=off`）接受，正式區回 403。

## 不用 Docker：systemd

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

- **web 不做身分驗證**，所以 `newsletter-web.service` 只綁 `127.0.0.1:8787`。要從外面連，得在前面放一個做到[入口閘門的約定](#入口閘門的約定)的閘門；這個 repo 不提供。
- 貼 Claude 的 OAuth token：沒有閘門時 `/auth` 就在主 port，在 host 上開 `http://127.0.0.1:8787/auth`（遠端 host 用 `ssh -L 8787:127.0.0.1:8787 <host>` 後開 `http://localhost:8787/auth`），做法見 [llm/README.md](../packages/llm/README.md)「認證」。沒貼的話排程會以 exit 2 結束。
- worker 和 Docker 版是同一個程式，排程、補跑、`/status`、`/run` 的行為見上面步驟 5；`/status`、`/run` 只綁 `127.0.0.1:8790`。
- 看 web：`journalctl --user -u newsletter-web -f`；看排程：`journalctl --user -u newsletter-worker -f`（下次執行時間、開始、結束），細節在 `logs/<YYYY-MM>.log`。
- 手動跑一次：`curl -X POST http://127.0.0.1:8790/run`（會真的呼叫 Claude 並寄信，約數分鐘、用掉訂閱額度）；結果看 `curl -s http://127.0.0.1:8790/status` 的 `last`。
- 更新（`interests.md`、`config/`、程式等被追蹤的檔案）：
  `git -C ~/newsletter-ops pull && (cd ~/newsletter-ops && uv sync --locked --project apps/worker && uv sync --locked --project apps/web-server && cd apps/web-ui && npm ci && npm run build) && systemctl --user restart newsletter-web newsletter-worker`。
  （前端沒變動時，`npm ci && npm run build` 可以省略。）`restart` 會等執行中的每日流程收尾（最多 2 分鐘）。

**4. 驗收**

| 檢查 | 預期 |
| --- | --- |
| host 上 `curl -sI http://127.0.0.1:8787/reports` | `200` |
| host 上 `ss -ltn \| grep 8787` | 只有 `127.0.0.1:8787`，不是 `0.0.0.0` |
| 前面有放閘門的話 | 照 Docker 版步驟 4 驗收表裡經閘門的那幾列（區網入口、Tunnel 網域的 `/auth`、未登入的 `curl`） |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，在 host 上依序跑 `uv run --project apps/worker newsletter-collect`、
`uv run --project apps/worker newsletter-write`（或上面的手動排程）先產一份。
