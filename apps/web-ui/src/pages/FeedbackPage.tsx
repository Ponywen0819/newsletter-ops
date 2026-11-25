import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch } from '../hooks'
import type { Mark } from '../types'

const LABEL = { '+': '有用', '-': '沒用' } as const
const MAX_UIDS = 10 // 一則新聞最多併幾篇；擋掉亂湊的長網址，免得一開頁就打一堆請求
const CHOICES: { mark: Mark; text: string }[] = [
  { mark: '+', text: '改成 有用' },
  { mark: '-', text: '改成 沒用' },
  { mark: '', text: '取消標記' },
]

/** fetch 本身失敗（離線、連不上）時瀏覽器給的是英文的 TypeError，換成看得懂的說法 */
const readable = (error: Error) => (error instanceof TypeError ? '連不上伺服器，確認網路後重新整理這一頁。' : error.message)

/** email 連結的落地頁。信箱的安全掃描會自動開連結，所以開頁只讀，按「確認」才寫入。 */
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
  const [intent, setIntent] = useState<Mark | null>(null) // 在這頁改過的話，使用者最後要的標記
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
  if (error) return <Notice title="讀取失敗">{readable(error)}</Notice>
  if (!data) return <Loading />

  // 每個 uid 目前的標記：剛寫成功的以伺服器回傳為準，否則是載入時的
  const marks = data.map((target) => saved[target.uid] ?? target.mark)
  const first = marks[0] ?? ''
  const same = marks.every((mark) => mark === first)
  const want: Mark = intent ?? vote
  // 要的標記已經全部記下：顯示它，並能改成另一個或取消（在信裡按錯時不必回信件、也不必在整份晨報裡找）。
  // 沒記下（還沒確認、或有一篇寫入失敗）：照確認的流程，按鈕還在可以重試
  const settled = !failed && same && first === want

  const save = async (mark: Mark) => {
    setIntent(mark)
    setBusy(true)
    const result = await api.feedbackAll(uids, mark)
    setSaved((prev) => ({ ...prev, ...Object.fromEntries(result.saved.map((row) => [row.uid, row.mark])) }))
    setFailed(result.failed)
    setBusy(false)
  }

  return (
    <>
      <h1>回饋</h1>
      {data.map((target) => (
        <p key={target.uid}>「{target.title || `${target.date} 晨報裡的一則新聞`}」</p>
      ))}
      {settled ? (
        <>
          <p role="status">{want ? `已記下 ${LABEL[want]}。` : '已取消標記。'}</p>
          <div className="auth-actions">
            {CHOICES.filter((c) => c.mark !== want).map((c) => (
              <button key={c.mark || 'clear'} type="button" className="auth-btn" disabled={busy} onClick={() => void save(c.mark)}>
                {c.text}
              </button>
            ))}
          </div>
        </>
      ) : (
        <>
          {same && first && (
            <p>
              目前標記是 {LABEL[first]}，確認後會{want ? `改成 ${LABEL[want]}` : '取消標記'}。
            </p>
          )}
          {!same && <p>這幾篇目前的標記不一致，確認後會{want ? `統一標為 ${LABEL[want]}` : '全部取消標記'}。</p>}
          {/* 沿用 /auth 頁的按鈕樣式 */}
          <button type="button" className="auth-btn auth-primary" disabled={busy} onClick={() => void save(want)}>
            {want ? `確認標為 ${LABEL[want]}` : '確認取消標記'}
          </button>
        </>
      )}
      {failed && <p role="status">儲存失敗，請重試；若一直失敗，重新整理頁面。</p>}
      <p>
        <Link to={`/reports/${data[0]!.date}`}>回 {data[0]!.date} 晨報</Link>
      </p>
    </>
  )
}
