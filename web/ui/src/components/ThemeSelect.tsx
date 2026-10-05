import { useState } from 'react'
import { readTheme, setTheme, type Theme } from '../theme'

export function ThemeSelect() {
  const [theme, setCurrent] = useState<Theme>(readTheme)
  return (
    <select
      className="theme-select"
      aria-label="配色"
      value={theme}
      onChange={(e) => {
        const next = e.target.value as Theme
        setTheme(next)
        setCurrent(next)
      }}
    >
      <option value="auto">自動</option>
      <option value="light">淺色</option>
      <option value="dark">深色</option>
    </select>
  )
}
