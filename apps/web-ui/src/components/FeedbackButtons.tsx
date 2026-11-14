import { type ReactNode, useState, useSyncExternalStore } from 'react'
import { api } from '../api'
import { check } from '../connectivity'
import { enqueue, getQueue, patchCopies, subscribeQueue } from '../feedbackQueue'
import type { Mark, Marks } from '../types'

const BUTTONS: { mark: '+' | '-'; icon: string; label: string }[] = [
  { mark: '+', icon: 'M6 15l6-6 6 6', label: '有用' },
  { mark: '-', icon: 'M6 9l6 6 6-6', label: '沒用' },
]

interface Props {
  /** 併了多篇文章的新聞會有好幾個 uid，按一次對每個都投票 */
  uids: string[]
  marks: Marks
  onChange: (uid: string, mark: Mark) => void
  /** 同一排的其他按鈕（事實有誤） */
  children?: ReactNode
}

/**
 * 有用／沒用。再按一次＝取消。線上時亮不亮以伺服器回傳為準；多個 uid 要全部同一標記才亮。
 * 離線或連不上時先排隊（feedbackQueue.ts），畫面與本機副本立刻更新，旁邊標「待同步」，恢復連線後補送。
 */
export function FeedbackButtons({ uids, marks, onChange, children }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const queue = useSyncExternalStore(subscribeQueue, getQueue)
  const pending = uids.some((uid) => uid in queue)

  const first = marks[uids[0] ?? ''] ?? ''
  const mark: Mark = uids.every((uid) => (marks[uid] ?? '') === first) ? first : ''

  async function press(value: '+' | '-') {
    setBusy(true)
    setError('')
    const next: Mark = mark === value ? '' : value
    const status = await check()
    if (status === 'offline' || status === 'unreachable') {
      for (const uid of uids) {
        enqueue(uid, next, marks[uid] ?? '')
        onChange(uid, next)
        void patchCopies(uid, next)
      }
      setBusy(false)
      return
    }
    const { saved, failed } = await api.feedbackAll(uids, next)
    saved.forEach((row) => onChange(row.uid, row.mark))
    // Access 逾時時 fetch 只會丟 TypeError，要重新整理才能登入
    if (failed) setError('儲存失敗，請重試；若一直失敗，重新整理頁面')
    setBusy(false)
  }

  return (
    <div className="fb">
      {BUTTONS.map(({ mark: value, icon, label }) => (
        <button
          key={value}
          type="button"
          className="fb-btn"
          data-mark={value}
          aria-pressed={mark === value}
          aria-label={label}
          title={label}
          disabled={busy}
          onClick={() => press(value)}
        >
          <svg
            viewBox="0 0 24 24"
            width="18"
            height="18"
            aria-hidden="true"
            focusable="false"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d={icon} />
          </svg>
        </button>
      ))}
      {children}
      <span className="fb-msg" role="status">
        {error || (pending && '待同步')}
      </span>
    </div>
  )
}
