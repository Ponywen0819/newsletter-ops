import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch } from '../hooks'
import type { Mark } from '../types'

const LABEL = { '+': '有用', '-': '沒用' } as const
const MAX_UIDS = 10 // 一則新聞最多併幾篇；擋掉亂湊的長網址，免得一開頁就打一堆請求

/**
 * email 裡 有用／沒用 連結（/feedback/<uid>?v=…）的落地頁。信箱的安全掃描會自動開連結，
 * 所以開頁面只讀；要按下「確認」才 POST，與晨報頁的按鈕走同一支 API。
 * 併了多篇文章的新聞，連結的 uid 用逗號接起來（/feedback/<uid>,<uid>）：一次確認，對每個 uid 各投一票。
 */
export function FeedbackPage() {
  const { uid: param = '' } = useParams()
  const uids = [...new Set(param.split(',').filter(Boolean))]
  const badLink = uids.length === 0 || uids.length > MAX_UIDS
  const v = useSearchParams()[0].get('v')
  const vote = v === '-' ? '-' : v === '+' || v === ' ' ? '+' : null // 手打的 ?v=+ 會被解成空白，一併當 +
  const { data, error } = useFetch(() => (badLink ? Promise.resolve([]) : Promise.all(uids.map((uid) => api.feedbackTarget(uid)))), [param])
  const [saved, setSaved] = useState<Record<string, Mark>>({})
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  useDocumentTitle('回饋')

  if (!vote) return <Notice title="連結不完整">缺少「有用／沒用」的選擇，請回信件再按一次。</Notice>
  if (badLink) return <Notice title="連結不完整">這個連結看起來不對，請回信件再按一次。</Notice>
  if (error instanceof ApiError && error.status === 404) {
    return (
      <Notice title="找不到這則新聞">
        它不在任何一份晨報裡。回 <Link to="/reports">歷史晨報</Link>。
      </Notice>
    )
  }
  if (error) return <Notice title="讀取失敗">{error.message}</Notice>
  if (!data) return <Loading />

  // 每個 uid 目前的標記：剛寫成功的以伺服器回傳為準，否則是載入時的
  const marks = data.map((target) => saved[target.uid] ?? target.mark)
  const first = marks[0] ?? ''
  const same = marks.every((mark) => mark === first)

  const confirm = async () => {
    setBusy(true)
    const result = await api.feedbackAll(uids, vote)
    setSaved((prev) => ({ ...prev, ...Object.fromEntries(result.saved.map((row) => [row.uid, row.mark])) }))
    setFailed(result.failed)
    setBusy(false)
  }

  return (
    <>
      <h1>回饋</h1>
      {data.map((target) => (
        <p key={target.uid}>「{target.title || '這則新聞'}」</p>
      ))}
      {same && first === vote ? (
        <p>已記下 {LABEL[vote]}。</p>
      ) : (
        <>
          {same && first && (
            <p>
              目前標記是 {LABEL[first]}，確認後會改成 {LABEL[vote]}。
            </p>
          )}
          {!same && <p>這幾篇目前的標記不一致，確認後會統一標為 {LABEL[vote]}。</p>}
          {/* 沿用 /auth 頁的按鈕樣式 */}
          <button type="button" className="auth-btn auth-primary" disabled={busy} onClick={() => void confirm()}>
            確認標為 {LABEL[vote]}
          </button>
          {failed && <p role="status">儲存失敗，請重試；若一直失敗，重新整理頁面。</p>}
        </>
      )}
      <p>
        <Link to={`/reports/${data[0]!.date}`}>回 {data[0]!.date} 晨報</Link>
      </p>
    </>
  )
}
