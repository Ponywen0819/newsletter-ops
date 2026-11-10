import { useState } from 'react'
import { api } from '../api'
import { check } from '../connectivity'
import type { Flags } from '../types'

interface Props {
  uids: string[]
  flags: Flags
  onChange: (uid: string, flag: 'wrong' | '') => void
}

/**
 * 「事實有誤」：之後寫晨報時，這則萃取出的事件不再被當成背景，它對別則的否認也不算。再按一次＝取消。
 * 不排隊：離線時直接說做不到，不要讓人以為記下了。
 */
export function FactFlagButton({ uids, flags, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const flagged = uids.length > 0 && uids.every((uid) => flags[uid] === 'wrong')

  async function press() {
    setBusy(true)
    setError('')
    if ((await check()) !== 'ok') {
      setError('連不上伺服器，稍後再標')
      setBusy(false)
      return
    }
    const next = flagged ? '' : 'wrong'
    const results = await Promise.allSettled(uids.map((uid) => api.kgFlag(uid, next)))
    results.forEach((r) => r.status === 'fulfilled' && onChange(r.value.uid, r.value.flag))
    if (results.some((r) => r.status === 'rejected')) setError('儲存失敗，請重試')
    setBusy(false)
  }

  return (
    <>
      <button
        type="button"
        className="fb-btn fb-flag"
        aria-pressed={flagged}
        title="標記這則的事實有誤：之後的晨報不會再引用它"
        disabled={busy}
        onClick={press}
      >
        事實有誤
      </button>
      {error && (
        <span className="fb-msg" role="alert">
          {error}
        </span>
      )}
    </>
  )
}
