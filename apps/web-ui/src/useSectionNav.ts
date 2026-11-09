import { useCallback, useEffect, useRef, useState, type MouseEvent, type RefObject } from 'react'

// 與 styles/reader.css 的斷點同一個值。沒有 matchMedia（jsdom）時當作寬螢幕，鍵盤切換才測得到
export const isWide = () => window.matchMedia?.('(min-width: 1100px)').matches ?? true

/** 左欄目錄與右欄內文的連動（目前項、點擊捲動、#hash、scroll-spy）。右欄每項要帶 `data-unit="<id>"`。 */
export function useSectionNav(ids: string[], rootRef: RefObject<HTMLElement | null>, navRef: RefObject<HTMLElement | null>) {
  const initial = () => {
    const fromHash = window.location.hash.slice(1)
    return ids.includes(fromHash) ? fromHash : (ids[0] ?? '')
  }
  const [chosen, setActiveId] = useState(initial)
  // ids 晚到（資料還在載入時就掛上）或換了一批：選的那項不在裡面就照 hash／第一項算，不必多等一輪 effect
  const activeId = ids.includes(chosen) ? chosen : initial()
  // 點目錄／按 j／k 之後的捲動動畫期間，不讓 scroll-spy 把剛選的那一則改掉
  const lockUntil = useRef(0)

  const scrollToUnit = useCallback(
    (id: string, instant = false) => {
      const unit = rootRef.current?.querySelector<HTMLElement>(`[data-unit="${id}"]`)
      if (!unit) return
      // 該分類的第一則：捲到分類標題，才看得出它屬於哪一類
      const prev = unit.previousElementSibling
      const target = prev?.classList.contains('group-title') ? prev : unit
      setActiveId(id)
      lockUntil.current = Date.now() + 800
      target.scrollIntoView?.({ block: 'start', behavior: instant ? 'instant' : 'auto' })
    },
    [rootRef],
  )

  const goTo = useCallback(
    (id: string) => {
      // 沿用 history.state：React Router 把自己的 key／idx 存在裡面，換成 null 會弄壞上一頁
      window.history.replaceState(window.history.state, '', `#${id}`)
      scrollToUnit(id)
    },
    [scrollToUnit],
  )

  // 帶修飾鍵的點擊（新分頁開啟）照舊
  const onLinkClick = useCallback(
    (id: string) => (e: MouseEvent) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return
      e.preventDefault()
      goTo(id)
    },
    [goTo],
  )

  // 開頁時的 #hash 只捲一次：恢復連線重抓資料（ids 換成新的陣列）時不要把讀者拉回去
  const openedAt = useRef(false)
  useEffect(() => {
    if (openedAt.current || !ids.length) return
    openedAt.current = true
    const id = window.location.hash.slice(1)
    if (ids.includes(id)) scrollToUnit(id, true)
  }, [ids, scrollToUnit])

  useEffect(() => {
    const onHashChange = () => {
      const id = window.location.hash.slice(1)
      if (ids.includes(id)) scrollToUnit(id)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [ids, scrollToUnit])

  // 開頭已越過頁面上方 30% 的最後一項是目前項；捲到底時是最後一項
  useEffect(() => {
    const onScroll = () => {
      if (Date.now() < lockUntil.current || !isWide()) return
      const units = [...(rootRef.current?.querySelectorAll<HTMLElement>('[data-unit]') ?? [])]
      let id = units[0]?.dataset.unit
      for (const unit of units) {
        if (unit.getBoundingClientRect().top > window.innerHeight * 0.3) break
        id = unit.dataset.unit
      }
      if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) id = units.at(-1)?.dataset.unit
      if (id) setActiveId(id)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [rootRef])

  // 只捲目錄自己：scrollIntoView 會干擾頁面正在進行的捲動
  useEffect(() => {
    const nav = navRef.current
    const item = nav?.querySelector('[aria-current="true"]')
    if (!nav || !item) return
    const box = nav.getBoundingClientRect()
    const rect = item.getBoundingClientRect()
    if (rect.top < box.top) nav.scrollTop -= box.top - rect.top
    else if (rect.bottom > box.bottom) nav.scrollTop += rect.bottom - box.bottom
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- activeId 變了要重新對齊
  }, [activeId, navRef])

  return { activeId, goTo, onLinkClick }
}
