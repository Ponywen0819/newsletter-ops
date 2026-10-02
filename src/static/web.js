// web.py 的前端：每則的 👍／👎。事件委派，頁面上有幾顆按鈕都只掛一個監聽。
// 按已亮起的那顆＝取消（送 mark ""）；按另一顆＝覆蓋。狀態以伺服器回傳的 mark 為準。
document.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('.fb-btn');
  if (!btn) return;
  const box = btn.closest('.fb');
  const msg = box.querySelector('.fb-msg');
  const buttons = box.querySelectorAll('.fb-btn');
  const mark = btn.getAttribute('aria-pressed') === 'true' ? '' : btn.dataset.mark;
  buttons.forEach((b) => { b.disabled = true; });
  msg.textContent = '';
  try {
    const res = await fetch('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uid: box.dataset.uid, mark }),
    });
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mark === data.mark)));
  } catch (err) {
    // Cloudflare Access 的登入逾時會把 POST 導去登入頁，fetch 只會丟 TypeError；重新整理才會重新登入
    msg.textContent = '儲存失敗，請重試；若一直失敗，重新整理頁面';
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
});
