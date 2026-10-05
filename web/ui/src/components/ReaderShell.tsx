import type { ReactNode, Ref } from 'react'

/**
 * 桌機版面（≥1100px）的兩欄：左欄放標題（晨報頁還有目錄），右欄是白底內文。
 * reader 路由的每個狀態（載入中、出錯、找不到、列表、晨報）都放在它裡面，外框才不會隨內容改變；
 * 窄螢幕沒有樣式，兩欄只是依序排下來。
 */
export function ReaderShell({ side, children, ref }: { side?: ReactNode; children: ReactNode; ref?: Ref<HTMLDivElement> }) {
  return (
    <div ref={ref} className="reader">
      <div className="reader-side">{side}</div>
      <div className="reader-body">{children}</div>
    </div>
  )
}
