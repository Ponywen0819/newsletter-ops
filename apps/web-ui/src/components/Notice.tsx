import type { ReactNode } from 'react'
import { useDocumentTitle } from '../hooks'

/** 只有一個標題與一段話的頁面（找不到、尚未產出、讀取失敗）。 */
export function Notice({ title, children }: { title: string; children?: ReactNode }) {
  useDocumentTitle(title)
  return (
    <>
      <h1>{title}</h1>
      {children && <p>{children}</p>}
    </>
  )
}

export function Loading() {
  return <p className="loading">載入中…</p>
}
