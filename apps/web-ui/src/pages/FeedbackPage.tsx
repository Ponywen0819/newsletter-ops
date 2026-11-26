import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import type { Mark } from '../types'

const LABEL = { '+': '有用', '-': '沒用' } as const
const MAX_UIDS = 10 // 一則新聞最多併幾篇；擋掉亂湊的長網址，免得一開頁就打一堆請求
const CHOICES: { mark: Mark; text: string }[] = [
  { mark: '+', text: '改成 有用' },
  { mark: '-', text: '改成 沒用' },
  { mark: '', text: '取消標記' },
]

/** fetch 本身失敗（離線、連不上）時瀏覽器給的是英文的 TypeError，換成看得懂的說法 */
const readable = (error: Error) => (error instanceof TypeError ? '連不上伺服器，確認網路後再試一次。' : error.message)

/** email 連結的落地頁。信箱的安全掃描會自動開連結，所以開頁只讀，按「確認」才寫入。 */
export function FeedbackPage() {
  const { uid: param = '' } = useParams()
  const uids = [...new Set(param.split(',').filter(Boolean))]
  const badLink = uids.length === 0 || uids.length > MAX_UIDS
  const v = useSearchParams()[0].get('v')
  const vote = v === '-' ? '-' : v === '+' || v === ' ' ? '+' : null // 手打的 ?v=+ 會被解成空白，一併當 +
  const [retry, setRetry] = useState(0)
  const { data, error } = useFetch(
    () => (badLink ? Promise.resolve([]) : Promise.all(uids.map((uid) => api.feedbackTarget(uid)))),
    [param, useRecovered(), retry],
  )
  const [saved, setSaved] = useState<Record<string, Mark>>({})
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // 確認過一次（全部寫入成功）之後就一直是「目前的標記＋改標按鈕」；之後改標失敗也留在這裡，不退回確認句
  const [confirmed, setConfirmed] = useState(false)
  const statusRef = useRef<HTMLParagraphElement>(null)
  useDocumentTitle('回饋')
  useEffect(() => {
    if (confirmed) statusRef.current?.focus() // 確認鈕換成改標按鈕：焦點不要掉回頁首
  }, [confirmed])

  if (!vote) return <Notice title="連結不完整">缺少「有用／沒用」的選擇，請回信件再按一次。</Notice>
  if (badLink) return <Notice title="連結不完整">這個連結看起來不對，請回信件再按一次。</Notice>
  if (error instanceof ApiError && error.status === 404) {
    return (
      <Notice title="找不到這則新聞">
        它不在任何一份晨報裡。回 <Link to="/reports">歷史晨報</Link>。
      </Notice>
    )
  }
  if (error)
    return (
      <>
        <Notice title="讀取失敗">{readable(error)}</Notice>
        <p>
          <button type="button" className="auth-btn auth-primary" onClick={() => setRetry((n) => n + 1)}>
            再試一次
          </button>
        </p>
      </>
    )
  if (!data) return <Loading />

  // 每個 uid 目前的標記：剛寫成功的以伺服器回傳為準，否則是載入時的
  const marks = data.map((target) => saved[target.uid] ?? target.mark)
  const first = marks[0] ?? ''
  const same = marks.every((mark) => mark === first)
  const settled = confirmed || (same && first === vote) // 信裡選的已經記下了，不必再確認

  const save = async (mark: Mark) => {
    setBusy(true)
    const result = await api.feedbackAll(uids, mark)
    setSaved((prev) => ({ ...prev, ...Object.fromEntries(result.saved.map((row) => [row.uid, row.mark])) }))
    setFailed(result.failed)
    if (!result.failed) setConfirmed(true)
    setBusy(false)
  }

  const state = !same ? '這幾篇目前的標記不一致。' : first ? `已記下 ${LABEL[first]}。` : '已取消標記。'
  return (
    <>
      <h1>回饋</h1>
      {data.map((target) => (
        <p key={target.uid}>「{target.title || `${target.date} 晨報裡的一則新聞`}」</p>
      ))}
      {/* 常駐的 status 區：內容換了螢幕閱讀器才唸得出來 */}
      <p role="status" ref={statusRef} tabIndex={-1}>
        {settled && state}
        {failed && ' 儲存失敗，請重試；若一直失敗，重新整理頁面。'}
      </p>
      {settled ? (
        // 在信裡按錯了：這裡就能改或取消，不必回信件、也不必在整份晨報裡找那一則
        <div className="auth-actions">
          {CHOICES.filter((c) => !same || c.mark !== first).map((c) => (
            <button key={c.mark || 'clear'} type="button" className="auth-btn" disabled={busy} onClick={() => void save(c.mark)}>
              {c.text}
            </button>
          ))}
        </div>
      ) : (
        <>
          {same && first && (
            <p>
              目前標記是 {LABEL[first]}，確認後會改成 {LABEL[vote]}。
            </p>
          )}
          {!same && <p>這幾篇目前的標記不一致，確認後會統一標為 {LABEL[vote]}。</p>}
          {/* 沿用 /auth 頁的按鈕樣式 */}
          <button type="button" className="auth-btn auth-primary" disabled={busy} onClick={() => void save(vote)}>
            確認標為 {LABEL[vote]}
          </button>
        </>
      )}
      <p>
        <Link to={`/reports/${data[0]!.date}`}>回 {data[0]!.date} 晨報</Link>
      </p>
    </>
  )
}
