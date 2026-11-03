import { useState } from 'react'
import { api } from '../api'
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
}

/** 有用／沒用。再按一次＝取消。亮不亮以伺服器回傳為準；多個 uid 要全部同一標記才亮。 */
export function FeedbackButtons({ uids, marks, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const first = marks[uids[0] ?? ''] ?? ''
  const mark: Mark = uids.every((uid) => (marks[uid] ?? '') === first) ? first : ''

  async function press(value: '+' | '-') {
    setBusy(true)
    setError('')
    const { saved, failed } = await api.feedbackAll(uids, mark === value ? '' : value)
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
      <span className="fb-msg" role="status">
        {error}
      </span>
    </div>
  )
}
