import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Notice } from './Notice'
import { ReaderShell } from './ReaderShell'

// 各瀏覽器載入分包（lazy 的頁面）失敗時的訊息：Chrome、Firefox、Safari
const CHUNK_FAILED = /dynamically imported module|Importing a module script failed|error loading dynamically imported module/i

/** lazy 頁面的程式載不到：多半是新版 service worker 接手、清掉了舊版的分包，也可能是離線又沒有快取。 */
export function isChunkError(error: unknown): boolean {
  return error instanceof Error && CHUNK_FAILED.test(error.message)
}

function Failed({ error }: { error: unknown }) {
  const stale = isChunkError(error)
  return (
    <>
      <Notice title={stale ? '網頁已更新' : '畫面出錯了'}>
        {stale
          ? '這一頁要用到的程式載不到（網頁剛更新版本，或目前離線）。重新載入就會換成新版。'
          : '這一頁畫到一半出錯了。重新載入再試一次；一直發生的話請回報。'}
      </Notice>
      <p>
        <button type="button" className="auth-btn auth-primary" onClick={() => location.reload()}>
          重新載入
        </button>
      </p>
    </>
  )
}

/** 頁面（含 lazy 載入）出錯時只換掉內容區，頁首與導覽照常能用。Layout 以路徑當 key，換頁就重來。 */
export class RouteErrorBoundary extends Component<{ children: ReactNode; reader?: boolean }, { error: unknown }> {
  state: { error: unknown } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(error, info.componentStack)
  }

  render() {
    if (this.state.error == null) return this.props.children
    const failed = <Failed error={this.state.error} />
    // 兩欄版面的 #content（跳到主要內容的目標）與外框都在 ReaderShell 裡
    return this.props.reader ? <ReaderShell>{failed}</ReaderShell> : failed
  }
}
