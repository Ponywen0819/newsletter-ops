import { useState, type FormEvent } from 'react'
import { api } from '../api'
import { Loading } from '../components/Notice'
import { useDocumentTitle, useFetch } from '../hooks'
import { useSession } from '../session'
import type { AuthResult, AuthStatus } from '../types'
import { NotFoundPage } from './NotFoundPage'

const STATE_LABEL = {
  ok: () => '已授權',
  expiring: (days: number) => `即將到期（剩 ${days} 天），請重新貼上新的 token`,
  expired: () => '預計已過期，請重新貼上新的 token',
}

function StatusView({ status }: { status: AuthStatus }) {
  return (
    <>
      <dl className="auth-status">
        <dt>狀態</dt>
        {status.state === 'missing' ? (
          <dd className="auth-state auth-missing">尚未授權</dd>
        ) : (
          <>
            <dd className={`auth-state auth-${status.state}`}>{STATE_LABEL[status.state](status.days_left)}</dd>
            <dt>Token</dt>
            <dd>
              <code>…{status.tail}</code>（只顯示尾 4 碼）
            </dd>
            <dt>儲存日期</dt>
            <dd>{status.saved_at}</dd>
            <dt>預計到期</dt>
            <dd>
              {status.expires_at}
              <span className="auth-hint">（以一年效期推算，實際以伺服器為準）</span>
            </dd>
          </>
        )}
      </dl>
      {status.state === 'missing' && status.env_token && (
        <p className="auth-note">
          這個程序的環境有 <code>CLAUDE_CODE_OAUTH_TOKEN</code>；排程若也設了同一個環境變數，agent_run.py
          會用它（優先序在這裡儲存的 token 之後）。
        </p>
      )}
    </>
  )
}

type Message = { kind: 'info' | 'ok' | 'err'; text: string }

/** token 只留在輸入框，成功後清掉；不寫進 URL 或瀏覽器的任何儲存空間。 */
function AuthPanel() {
  useDocumentTitle('Claude 授權')
  const { data: loaded, error: loadError } = useFetch(api.auth.status, [])
  const [updated, setUpdated] = useState<AuthStatus>()
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<Message>()
  const status = updated ?? loaded

  async function run(action: () => Promise<AuthResult>): Promise<boolean> {
    setBusy(true)
    setMessage({ kind: 'info', text: '處理中…（會實際呼叫一次 Claude，可能要幾秒）' })
    try {
      const result = await action()
      setUpdated(result.status)
      setMessage({ kind: 'ok', text: result.message || '完成' })
      return true
    } catch (err) {
      setMessage({ kind: 'err', text: (err instanceof Error && err.message) || '失敗，請重試' })
      return false
    } finally {
      setBusy(false)
    }
  }

  async function submit(ev: FormEvent) {
    ev.preventDefault()
    if (await run(() => api.auth.saveToken(token))) setToken('')
  }

  function revoke() {
    if (window.confirm('刪除這裡儲存的 token？（token 在 Anthropic 端仍然有效）')) void run(api.auth.revoke)
  }

  return (
    <>
      <h1>Claude 授權</h1>
      <p>
        無人值守執行（<code>agent_run.py</code>）只用 OAuth 的訂閱額度，<strong>不使用 API key</strong>
        ，也不會因為額度用完而自動改用別的認證方式。這個頁面只能從本機開啟。
      </p>
      <div aria-live="polite">
        {status ? <StatusView status={status} /> : loadError ? null : <Loading />}
      </div>
      <h2>貼上 token</h2>
      <ol className="auth-steps">
        <li>
          在<strong>自己的電腦</strong>開終端機，執行 <code>claude setup-token</code>
        </li>
        <li>在瀏覽器完成授權</li>
        <li>複製終端機印出的 token（只會印一次，CLI 不會幫你存）</li>
        <li>貼到下面送出。伺服器會先實際呼叫一次 Claude 驗證（用掉極少的訂閱額度），通過才儲存</li>
      </ol>
      <form id="auth-form" autoComplete="off" onSubmit={submit}>
        <input
          id="auth-token"
          type="password"
          name="token"
          autoComplete="off"
          spellCheck={false}
          placeholder="貼上 token"
          aria-label="OAuth token"
          required
          value={token}
          onChange={(ev) => setToken(ev.target.value)}
        />
        <button type="submit" className="auth-btn auth-primary" disabled={busy}>
          驗證並儲存
        </button>
      </form>
      <p className={`auth-msg${message && message.kind !== 'info' ? ` auth-${message.kind}` : ''}`} role="status">
        {message?.text ?? (loadError ? loadError.message : '')}
      </p>
      <div className="auth-actions">
        <button type="button" className="auth-btn" disabled={busy} onClick={() => void run(api.auth.test)}>
          測試連線
        </button>
        <button type="button" className="auth-btn auth-danger" disabled={busy} onClick={revoke}>
          刪除已存的 token
        </button>
      </div>
      <p className="auth-note">
        token 需要 Pro／Max／Team／Enterprise 方案，效期一年；到期前這裡會提醒。
        訂閱有使用額度，額度用完時晨報會失敗（exit 6），不會自動改用 API key。
        刪除只會移除這裡儲存的檔案，token 在 Anthropic 端仍然有效。
      </p>
    </>
  )
}

/** /auth 只服務本機：後端對非本機一律 404，這裡也不畫出來。 */
export function AuthPage() {
  const session = useSession()
  if (!session) return <Loading />
  return session.local ? <AuthPanel /> : <NotFoundPage />
}
