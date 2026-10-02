// /auth 頁面的前端：貼上 token（伺服器驗證通過才儲存）、測試連線、刪除已存的 token。
// 狀態區塊由伺服器產生（內容已 escape），回應裡的 html 直接換進去；訊息一律用 textContent。
// token 只留在輸入框，成功後清掉；不寫進 URL 或瀏覽器的任何儲存空間。
const form = document.getElementById('auth-form');
const input = document.getElementById('auth-token');
const msg = document.getElementById('auth-msg');
const statusBox = document.getElementById('auth-status');

const setBusy = (busy) => document.querySelectorAll('.auth-btn').forEach((b) => { b.disabled = busy; });

async function call(path, payload) {
  setBusy(true);
  msg.className = 'auth-msg';
  msg.textContent = '處理中…（會實際呼叫一次 Claude，可能要幾秒）';
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* 回應不是 JSON：下面用狀態碼報錯 */ }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    if (data.html) statusBox.innerHTML = data.html;
    msg.className = 'auth-msg auth-ok';
    msg.textContent = data.message || '完成';
    return true;
  } catch (err) {
    msg.className = 'auth-msg auth-err';
    msg.textContent = err.message || '失敗，請重試';
    return false;
  } finally {
    setBusy(false);
  }
}

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (await call('/auth/token', { token: input.value })) input.value = '';
});

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-action]');
  if (!btn) return;
  if (btn.dataset.action === 'test') call('/auth/test', {});
  if (btn.dataset.action === 'revoke' && confirm('刪除這裡儲存的 token？（token 在 Anthropic 端仍然有效）')) {
    call('/auth/revoke', {});
  }
});
