import { useState } from 'react'
import { api } from '../api'
import type { Mark } from '../types'

// 24×24 viewBox 的 chevron：向上＝有用、向下＝沒用。顏色吃 currentColor，亮不亮只靠 CSS 切換
const BUTTONS: { mark: '+' | '-'; icon: string; label: string }[] = [
  { mark: '+', icon: 'M6 15l6-6 6 6', label: '有用' },
  { mark: '-', icon: 'M6 9l6 6 6-6', label: '沒用' },
]

interface Props {
  uid: string
  mark: Mark
  /** 伺服器確認寫入後呼叫，帶回實際記下的 mark */
  onChange: (uid: string, mark: Mark) => void
}

/**
 * 每則的有用／沒用（上／下箭頭圖示）。按已亮起的那顆＝取消（送 mark ""）；按另一顆＝覆蓋。
 * 亮不亮以伺服器回傳的 mark 為準，所以寫入失敗時畫面不會假裝成功。
 */
export function FeedbackButtons({ uid, mark, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function press(value: '+' | '-') {
    setBusy(true)
    setError('')
    try {
      const saved = await api.feedback(uid, mark === value ? '' : value)
      onChange(uid, saved.mark)
    } catch {
      // Cloudflare Access 的登入逾時會把 POST 導去登入頁，fetch 只會丟 TypeError；重新整理才會重新登入
      setError('儲存失敗，請重試；若一直失敗，重新整理頁面')
    } finally {
      setBusy(false)
    }
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
