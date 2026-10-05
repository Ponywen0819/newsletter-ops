# notify

外圍：報告 → email → 寄出，只用標準庫、只依賴 [shared](../shared/README.md)。[agent](../agent/README.md) 不認得它；之後加別的通知管道也放這裡。
專案全貌見[根目錄 README](../README.md)。

```
src/newsletter_notify/
  render_email.py        email 層：報告結構 → inline-CSS HTML（reports/<date>.html）（newsletter-render）
  send_email.py          寄信層：Gmail SMTP 寄出 email HTML，不依賴 Claude 的 Gmail connector（newsletter-send）
```

## 使用

```bash
uv run newsletter-render | uv run newsletter-send   # 寄出當日 email
```

`render_email.py` 的 stdout 是一行 JSON，`send_email.py` 從 stdin 讀它；排程（根目錄的 `run_daily.sh`）先收進變數再餵給 `send_email.py`。

`send_email.py` 讀環境變數 `GMAIL_USER`、`GMAIL_APP_PASSWORD`（Google 帳號的應用程式密碼，需先開兩步驟驗證）、
`NEWSLETTER_MAIL_TO`（逗號分隔，沒設就寄給自己）。加 `--dry-run` 只印標頭不寄。

## 版型

email 與網頁吃 shared 的 `report_data.py` 解出的同一份結構，由 `render_email.py` 排成 inline-CSS HTML；
兩邊版型各自維護（網頁在 [web/ui](../web/ui/README.md)）。哪些條目可以投票由 `report_data.py` 的 `votable` 決定，兩邊一致。

## email 裡的「有用／沒用」連結

email 的連結文字帶 emoji（「👍 有用／👎 沒用」；網頁上的按鈕是箭頭圖示）。設環境變數 `NEWSLETTER_BASE_URL`（對外網址，如 `https://news.example.com`，要 `http(s)://` 開頭）後，
`render_email.py` 會在每則**主要新聞**底下加兩個連結，指向 `<base>/feedback/<uid>?v=%2B`（有用）／`?v=-`（沒用），手機看信也能回饋。

- 與網頁一致：「其餘收錄」那種沒有標題段落的整張單行清單不放連結；併了多篇文章的新聞（報告裡連著好幾行 mark，每篇一個 uid）只放一組，
  uid 用逗號接起來 `<base>/feedback/<uid>,<uid>?v=…`，確認頁一次對每個 uid 各投一票（單一 uid 的舊連結照常可用）。
- **連結不會一點就寫入**：信箱的安全掃描會自動開連結，所以 GET 只顯示「確認標為 有用」的頁面（`web/ui/src/pages/FeedbackPage.tsx`，
  資料來自 `GET /api/feedback/<uid>`，純讀取），按了確認才 `POST /api/feedback`。已經是同一個標記就只顯示「已記下」；
  標記不同則說明會覆蓋。
- 沒設 `NEWSLETTER_BASE_URL`（本機測試）就不加按鈕；格式不對會在 stderr 警告並不加。
- 網址要是 Tunnel + Access 保護的那個網域：點連結時 Access 會先要求登入，掃描器看到的只是登入頁。
- Docker 部署在 `.env` 設；systemd／cron 部署在 `~/.config/newsletter-ops/env` 設（見 [deploy/README.md](../deploy/README.md)）。

## 自我檢查

```bash
uv run newsletter-render --selftest   # email 輸出
uv run newsletter-send --selftest
```
