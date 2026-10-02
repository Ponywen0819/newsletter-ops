import { useState } from 'react'
import { api } from '../api'
import type { Mark } from '../types'

const BUTTONS: { mark: '+' | '-'; emoji: string; label: string }[] = [
  { mark: '+', emoji: '👍', label: '有用' },
  { mark: '-', emoji: '👎', label: '沒用' },
]

interface Props {
  uid: string
  mark: Mark
  /** 伺服器確認寫入後呼叫，帶回實際記下的 mark */
  onChange: (uid: string, mark: Mark) => void
}

/**
 * 每則的 👍／👎。按已亮起的那顆＝取消（送 mark ""）；按另一顆＝覆蓋。
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
      setError('儲存失敗，請重試')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fb">
      {BUTTONS.map(({ mark: value, emoji, label }) => (
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
          {emoji}
        </button>
      ))}
      <span className="fb-msg" role="status">
        {error}
      </span>
    </div>
  )
}
