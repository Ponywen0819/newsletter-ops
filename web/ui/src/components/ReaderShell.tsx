import type { ReactNode, Ref } from 'react'

/** ≥1100px 的兩欄版面；reader 路由的每個狀態都放在裡面，外框才不會跳動。 */
export function ReaderShell({ side, children, ref }: { side?: ReactNode; children: ReactNode; ref?: Ref<HTMLDivElement> }) {
  return (
    <div ref={ref} className="reader">
      <div className="reader-side">{side}</div>
      <div className="reader-body">{children}</div>
    </div>
  )
}
