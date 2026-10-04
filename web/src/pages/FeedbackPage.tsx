import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch } from '../hooks'
import type { Mark } from '../types'

const LABEL = { '+': '👍 有用', '-': '👎 沒用' } as const

/**
 * email 裡 👍／👎 連結（/feedback/<uid>?v=…）的落地頁。信箱的安全掃描會自動開連結，
 * 所以開頁面只讀；要按下「確認」才 POST，與晨報頁的按鈕走同一支 API。
 */
export function FeedbackPage() {
  const { uid = '' } = useParams()
  const v = useSearchParams()[0].get('v')
  const vote = v === '-' ? '-' : v === '+' || v === ' ' ? '+' : null // 手打的 ?v=+ 會被解成空白，一併當 +
  const { data, error } = useFetch(() => api.feedbackTarget(uid), [uid])
  const [saved, setSaved] = useState<Mark | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  useDocumentTitle('回饋')

  if (!vote) return <Notice title="連結不完整">缺少 👍／👎 的選擇，請回信件再按一次。</Notice>
  if (error instanceof ApiError && error.status === 404) {
    return (
      <Notice title="找不到這則新聞">
        它不在任何一份晨報裡。回 <Link to="/reports">歷史晨報</Link>。
      </Notice>
    )
  }
  if (error) return <Notice title="讀取失敗">{error.message}</Notice>
  if (!data) return <Loading />

  const current = saved ?? data.mark

  const confirm = async () => {
    setBusy(true)
    setFailed(false)
    try {
      setSaved((await api.feedback(uid, vote)).mark)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1>回饋</h1>
      <p>「{data.title || '這則新聞'}」</p>
      {current === vote ? (
        <p>已記下 {LABEL[vote]}。</p>
      ) : (
        <>
          {current && (
            <p>
              目前標記是 {LABEL[current]}，確認後會改成 {LABEL[vote]}。
            </p>
          )}
          {/* 沿用 /auth 頁的按鈕樣式 */}
          <button type="button" className="auth-btn auth-primary" disabled={busy} onClick={() => void confirm()}>
            確認標為 {LABEL[vote]}
          </button>
          {failed && <p role="status">儲存失敗，請重試；若一直失敗，重新整理頁面。</p>}
        </>
      )}
      <p>
        <Link to={`/reports/${data.date}`}>回 {data.date} 晨報</Link>
      </p>
    </>
  )
}
