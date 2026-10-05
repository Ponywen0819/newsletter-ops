import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

// 與 styles.css 的閱讀器斷點同一個值。沒有 matchMedia（jsdom）時當作寬螢幕，鍵盤切換才測得到
export const isWide = () => window.matchMedia?.('(min-width: 1100px)').matches ?? true

/**
 * 左欄目錄（≥1100px）和右欄內文的連動：目前是哪一項、點目錄捲過去、網址 #hash、捲動時跟著亮起。
 * 右欄的每一項要帶 `data-unit="<id>"`（ids 是它們的 id，依畫面順序），root 是包住這些項目的元素；
 * nav 是目錄自己，目前這一項（aria-current="true"）超出目錄的可見範圍時只捲目錄。
 * 晨報頁（一則一項）與歷史晨報（一個月一項）共用。
 */
export function useSectionNav(ids: string[], rootRef: RefObject<HTMLElement | null>, navRef: RefObject<HTMLElement | null>) {
  const [activeId, setActiveId] = useState(() => {
    const fromHash = window.location.hash.slice(1)
    return ids.includes(fromHash) ? fromHash : (ids[0] ?? '')
  })
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
      // 平順與否交給 CSS 的 scroll-behavior（尊重 prefers-reduced-motion）；載入時帶 hash 直接到位
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

  // 網址帶 hash 開頁：直接捲到那一則
  useEffect(() => {
    const id = window.location.hash.slice(1)
    if (ids.includes(id)) scrollToUnit(id, true)
  }, [ids, scrollToUnit])

  // 上一頁／下一頁、手改網址的 hash
  useEffect(() => {
    const onHashChange = () => {
      const id = window.location.hash.slice(1)
      if (ids.includes(id)) scrollToUnit(id)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [ids, scrollToUnit])

  // scroll-spy：頁面上方 30% 那條線以上、最後一個開頭已經過線的單位就是目前這一則；捲到底時是最後一則
  // （最後幾則很短的話，捲到底也碰不到那條線）
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

  // 目錄比視窗長時，目前這一則要留在看得到的地方。只捲目錄自己：用 scrollIntoView 的話
  // 可能連帶影響頁面正在進行的捲動
  useEffect(() => {
    const nav = navRef.current
    const item = nav?.querySelector('[aria-current="true"]')
    if (!nav || !item) return
    const box = nav.getBoundingClientRect()
    const rect = item.getBoundingClientRect()
    if (rect.top < box.top) nav.scrollTop -= box.top - rect.top
    else if (rect.bottom > box.bottom) nav.scrollTop += rect.bottom - box.bottom
  }, [activeId, navRef])

  return { activeId, goTo }
}
