import type { InlineNode } from '../types'

// 連結的網址來自報告 Markdown；後端只收 http(s)，這裡再擋一次，javascript: 之類的不會變成連結
const SAFE_HREF = /^https?:\/\//i

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
              <a key={i} href={node.href}>
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
