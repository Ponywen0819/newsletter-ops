import { useState } from 'react'
import { api } from '../api'
import type { Mark, Marks } from '../types'

// 24×24 viewBox 的 chevron：向上＝有用、向下＝沒用。顏色吃 currentColor，亮不亮只靠 CSS 切換
const BUTTONS: { mark: '+' | '-'; icon: string; label: string }[] = [
  { mark: '+', icon: 'M6 15l6-6 6 6', label: '有用' },
  { mark: '-', icon: 'M6 9l6 6 6-6', label: '沒用' },
]

interface Props {
  /** 這一則掛的 uid。併了多篇文章的新聞會有好幾個：畫面上仍只有一組鈕，按下去對每個 uid 一起投票 */
  uids: string[]
  marks: Marks
  /** 伺服器確認寫入後，對每個寫入成功的 uid 呼叫一次，帶回實際記下的 mark */
  onChange: (uid: string, mark: Mark) => void
}

/**
 * 每則的有用／沒用（上／下箭頭圖示）。按已亮起的那顆＝取消（送 mark ""）；按另一顆＝覆蓋。
 * 亮不亮以伺服器回傳的 mark 為準，所以寫入失敗時畫面不會假裝成功。
 * 多個 uid 的亮起條件是「全部都是同一個標記」；只有部分標過（例如信裡只按了其中一篇）就都不亮，按下去會全部統一。
 */
export function FeedbackButtons({ uids, marks, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const first = marks[uids[0] ?? ''] ?? ''
  const mark: Mark = uids.every((uid) => (marks[uid] ?? '') === first) ? first : ''

  async function press(value: '+' | '-') {
    setBusy(true)
    setError('')
    const target = mark === value ? '' : value
    const results = await Promise.allSettled(uids.map((uid) => api.feedback(uid, target)))
    let failed = false
    results.forEach((result, i) => {
      if (result.status === 'fulfilled') onChange(uids[i]!, result.value.mark)
      else failed = true
    })
    // Cloudflare Access 的登入逾時會把 POST 導去登入頁，fetch 只會丟 TypeError；重新整理才會重新登入
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
