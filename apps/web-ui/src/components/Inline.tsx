import type { InlineNode } from '../types'

// 連結的網址來自報告 Markdown；後端只收 http(s)，這裡再擋一次，javascript: 之類的不會變成連結
const SAFE_HREF = /^https?:\/\//i
// 報告裡的連結都是外部原文：開新分頁，看完回來捲動位置與標記都還在；不帶出報告網址
export const EXTERNAL = { target: '_blank', rel: 'noopener noreferrer' } as const

export function Inline({ nodes }: { nodes: InlineNode[] }) {
  return (
    <>
      {nodes.map((node, i) => {
        switch (node.type) {
          case 'text':
            return node.text
          case 'strong':
            return (
              <strong key={i}>
                <Inline nodes={node.children} />
              </strong>
            )
          case 'link':
            return SAFE_HREF.test(node.href) ? (
              <a key={i} href={node.href} {...EXTERNAL}>
                <Inline nodes={node.children} />
              </a>
            ) : (
              <Inline key={i} nodes={node.children} />
            )
          case 'code':
            return <code key={i}>{node.text}</code>
        }
      })}
    </>
  )
}
