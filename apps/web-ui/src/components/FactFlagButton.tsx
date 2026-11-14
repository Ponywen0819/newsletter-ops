import { useState } from 'react'
import { api } from '../api'
import { check } from '../connectivity'
import type { Flags, KgFlag } from '../types'

interface Props {
  uids: string[]
  flags: Flags
  onChange: (uid: string, flag: KgFlag | '') => void
  /** 預設 wrong（事實有誤）；trusted（確認可信）只在知識圖譜用 */
  kind?: KgFlag
}

const LABEL: Record<KgFlag, { text: string; title: string }> = {
  wrong: { text: '事實有誤', title: '標記這則的事實有誤：之後的晨報不會再引用它' },
  trusted: { text: '確認可信', title: '標記這則可信：不會因為別則的否認被自動判定失效' },
}

/**
 * 事實判定。「事實有誤」：之後寫晨報時，這則萃取出的事件不再被當成背景，它對別則的否認也不算；
 * 「確認可信」：不被自動判定為有爭議或失效。一則只有一個判定，按另一個就換過去；再按一次＝取消。
 * 不排隊：離線時直接說做不到，不要讓人以為記下了。
 */
export function FactFlagButton({ uids, flags, onChange, kind = 'wrong' }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const flagged = uids.length > 0 && uids.every((uid) => flags[uid] === kind)

  async function press() {
    setBusy(true)
    setError('')
    if ((await check()) !== 'ok') {
      setError('連不上伺服器，稍後再標')
      setBusy(false)
      return
    }
    const next = flagged ? '' : kind
    const results = await Promise.allSettled(uids.map((uid) => api.kgFlag(uid, next)))
    results.forEach((r) => r.status === 'fulfilled' && onChange(r.value.uid, r.value.flag))
    if (results.some((r) => r.status === 'rejected')) setError('儲存失敗，請重試')
    setBusy(false)
  }

  return (
    <>
      <button
        type="button"
        className={`fb-btn fb-flag fb-flag-${kind}`}
        aria-pressed={flagged}
        title={LABEL[kind].title}
        disabled={busy}
        onClick={press}
      >
        {LABEL[kind].text}
      </button>
      {error && (
        <span className="fb-msg" role="alert">
          {error}
        </span>
      )}
    </>
  )
}
