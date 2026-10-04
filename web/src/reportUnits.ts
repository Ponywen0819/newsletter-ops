import type { Block, InlineNode, Report } from './types'

type TitleBlock = Extract<Block, { type: 'title' }>
type HeadingBlock = Extract<Block, { type: 'heading' }>
type CalloutBlock = Extract<Block, { type: 'callout' }>

/** 閱讀單位：筆電閱讀器右欄一次顯示一個。 */
export interface Unit {
  /** 同一份晨報內穩定：`s-<第一個區塊的序號>`，資料來源是 `s-sources`；也拿來當網址 hash */
  id: string
  /** 左清單上的文字 */
  label: string
  /** story＝段落＋緊接的清單；list＝沒有前導段落的整張清單；paragraph＝後面不接清單的段落；callout＝第二個以後的 callout（第一個是 Grouped.callout） */
  kind: 'callout' | 'story' | 'list' | 'paragraph' | 'sources'
  blocks: Block[]
}

export interface Group {
  /** 開頭（頭條）那組沒有分類標題 */
  heading: HeadingBlock | null
  label: string
  units: Unit[]
}

export interface Grouped {
  title: TitleBlock | null
  /** 今日頭條：固定顯示在最上面，不是閱讀單位——不在 units 裡，左清單不列、不參與上一則／下一則 */
  callout: CalloutBlock | null
  groups: Group[]
  /** 沒有資料來源就是 null */
  sources: Unit | null
  /** 依畫面順序攤平（各組的 unit，最後是資料來源）：上一則／下一則照這個走 */
  units: Unit[]
}

export function plainText(nodes: InlineNode[]): string {
  return nodes.map((n) => (n.type === 'text' || n.type === 'code' ? n.text : plainText(n.children))).join('')
}

/**
 * 把晨報的 blocks 切成「分類組 → 閱讀單位」。後端 JSON 不動，這裡只重新分組：
 * 每個區塊恰好出現一次（title、callout 在 Grouped 上，heading 在 Group 上，其餘都在某個 unit 裡），不遺失也不重複。
 * 「其餘收錄」那種每條自帶回饋鈕的單行清單沒有前導段落，整張清單算一個 unit，標籤用分類名。
 */
export function groupReport(report: Report): Grouped {
  const groups: Group[] = []
  let title: TitleBlock | null = null
  let callout: CalloutBlock | null = null
  let current: Group = { heading: null, label: '', units: [] }

  const flush = () => {
    if (current.heading || current.units.length) groups.push(current)
  }
  const add = (index: number, kind: Unit['kind'], label: string, blocks: Block[]) =>
    current.units.push({ id: `s-${index}`, kind, label: label.trim() || '（無標題）', blocks })

  const { blocks } = report
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i]!
    switch (block.type) {
      case 'title':
        if (!title) title = block
        else add(i, 'paragraph', block.title, [block]) // 理論上只有一個；多的不能丟，當一般單位
        break
      case 'heading':
        flush()
        current = { heading: block, label: plainText(block.inline), units: [] }
        break
      case 'callout':
        if (!callout) callout = block
        else add(i, 'callout', plainText(block.inline).slice(0, 30), [block]) // 理論上只有一個；多的不能丟，當一般單位
        break
      case 'paragraph': {
        const next = blocks[i + 1]
        if (next?.type === 'list') {
          add(i, 'story', plainText(block.inline), [block, next])
          i++
        } else {
          add(i, 'paragraph', plainText(block.inline), [block])
        }
        break
      }
      case 'list':
        add(i, 'list', current.label || '內容', [block])
        break
      case 'mark': {
        // mark 跟著前一個單位走（清單之後的獨立回饋鈕）；開頭就出現的孤兒 mark 自成一個單位
        const last = current.units.at(-1) ?? groups.at(-1)?.units.at(-1)
        if (last) last.blocks.push(block)
        else add(i, 'paragraph', '回饋', [block])
        break
      }
    }
  }
  flush()

  const sources: Unit | null = report.sources.length ? { id: 's-sources', kind: 'sources', label: '資料來源', blocks: [] } : null
  const units = groups.flatMap((g) => g.units)
  if (sources) units.push(sources)
  return { title, callout, groups, sources, units }
}
