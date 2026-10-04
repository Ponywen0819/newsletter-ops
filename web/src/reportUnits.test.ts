import { describe, expect, it } from 'vitest'
import { groupReport, plainText } from './reportUnits'
import { report as fixture, UID_A, UID_B, UID_C } from './test/fixtures'
import type { Block, InlineNode, Report } from './types'

const t = (text: string): InlineNode[] => [{ type: 'text', text }]
const heading = (text: string): Block => ({ type: 'heading', inline: t(text) })
const para = (text: string): Block => ({ type: 'paragraph', inline: [{ type: 'strong', children: [{ type: 'link', href: 'https://x.example', children: t(text) }] }] })
const list = (...items: { text: string; uid?: string }[]): Block => ({
  type: 'list',
  votable: true,
  items: items.map(({ text, uid }) => ({ inline: t(text), uids: uid ? [uid] : undefined })),
})
const make = (blocks: Block[], sources: Report['sources'] = []): Report => ({ title: 'T', subject: 'S', headline: 'H', blocks, sources })

// 與實際晨報同形：頭條、兩則新聞、一張「其餘收錄」（每條自帶回饋鈕、沒有前導段落）、一張沒有回饋鈕的備註
const real = make(
  [
    { type: 'title', title: '每日晨間簡報', date: '2026-10-04' },
    { type: 'callout', inline: t('今日頭條：G7 …') },
    heading('科技與 AI'),
    para('馬斯克證實與台積電洽談'),
    list({ text: '發生什麼：…' }, { text: '後續觀察：…', uid: UID_A }),
    para('加州檢察長傳喚 OpenAI'),
    list({ text: '發生什麼：…' }, { text: '後續觀察：…', uid: UID_B }),
    heading('其餘收錄'),
    list({ text: 'A', uid: '1' }, { text: 'B', uid: '2' }, { text: 'C', uid: '3' }),
    heading('本期備註'),
    list({ text: '備註一' }, { text: '備註二' }),
  ],
  [{ title: '來源', url: 'https://a.example/1' }],
)

describe('groupReport', () => {
  it('新聞、整張清單、資料來源各成一個單位；今日頭條不是單位，獨立放在 callout', () => {
    const g = groupReport(real)
    expect(g.title).toMatchObject({ type: 'title', title: '每日晨間簡報' })
    expect(g.callout).toMatchObject({ type: 'callout' })
    expect(g.groups.map((x) => x.label)).toEqual(['科技與 AI', '其餘收錄', '本期備註']) // 沒有分類標題、也沒有單位的開頭那組不留
    expect(g.units.map((u) => [u.id, u.kind, u.label])).toEqual([
      ['s-3', 'story', '馬斯克證實與台積電洽談'],
      ['s-5', 'story', '加州檢察長傳喚 OpenAI'],
      ['s-8', 'list', '其餘收錄'], // 沒有前導段落的清單：標籤用分類名
      ['s-10', 'list', '本期備註'],
      ['s-sources', 'sources', '資料來源'],
    ])
    expect(g.units.some((u) => u.blocks.includes(g.callout!))).toBe(false)
    expect(g.sources).toBe(g.units.at(-1))
  })

  it('第二個以後的 callout 不能丟，當一般單位（標籤取內文前 30 字）', () => {
    const second: Block = { type: 'callout', inline: t('提醒：' + '很長'.repeat(30)) }
    const g = groupReport(make([{ type: 'callout', inline: t('今日頭條：A') }, heading('H'), second]))
    expect(g.callout).toMatchObject({ inline: t('今日頭條：A') })
    expect(g.units).toHaveLength(1)
    expect(g.units[0]).toMatchObject({ kind: 'callout', blocks: [second] })
    expect(g.units[0]!.label).toHaveLength(30)
  })

  it('每個區塊恰好出現一次：不遺失、不重複', () => {
    for (const r of [real, fixture]) {
      const g = groupReport(r)
      const seen = [
        ...(g.title ? [g.title] : []),
        ...(g.callout ? [g.callout] : []),
        ...g.groups.flatMap((x) => [...(x.heading ? [x.heading] : []), ...x.units.flatMap((u) => u.blocks)]),
      ]
      expect(seen).toHaveLength(r.blocks.length)
      expect(new Set(seen)).toEqual(new Set(r.blocks)) // 同一份物件參照，沒有被複製或漏掉
    }
  })

  it('段落＋清單是一則新聞；其後的獨立 mark 歸給前一個單位', () => {
    const g = groupReport(fixture)
    const story = g.units.find((u) => u.kind === 'story')!
    expect(story.blocks.map((b) => b.type)).toEqual(['paragraph', 'list'])
    const rest = g.units.find((u) => u.label === '其餘收錄')!
    expect(rest.blocks.map((b) => b.type)).toEqual(['list', 'mark'])
    expect(rest.blocks[1]).toMatchObject({ uid: UID_C })
  })

  it('後面不接清單的段落自成一個單位', () => {
    const g = groupReport(make([heading('H'), para('孤單的段落'), heading('H2'), list({ text: 'x' })]))
    expect(g.units.map((u) => [u.kind, u.label])).toEqual([
      ['paragraph', '孤單的段落'],
      ['list', 'H2'],
    ])
  })

  it('邊界：空 blocks、沒有分類標題、沒有資料來源都不會丟例外', () => {
    expect(groupReport(make([]))).toEqual({ title: null, callout: null, groups: [], sources: null, units: [] })

    const flat = groupReport(make([para('舊格式標題'), list({ text: 'a' }), list({ text: 'b' })]))
    expect(flat.groups).toHaveLength(1)
    expect(flat.groups[0]!.heading).toBeNull()
    expect(flat.units.map((u) => [u.kind, u.label])).toEqual([
      ['story', '舊格式標題'],
      ['list', '內容'], // 沒有分類名可用
    ])
    expect(flat.sources).toBeNull()
  })

  it('邊界：孤兒 mark、空標題、多個 title 都不會遺失區塊', () => {
    const blocks: Block[] = [{ type: 'mark', uid: UID_A, votable: true }, { type: 'title', title: 'A', date: null }, { type: 'title', title: 'B', date: null }, para('')]
    const g = groupReport(make(blocks))
    const count = (g.title ? 1 : 0) + (g.callout ? 1 : 0) + g.units.reduce((n, u) => n + u.blocks.length, 0)
    expect(count).toBe(blocks.length)
    expect(g.units.every((u) => u.label.length > 0)).toBe(true)
  })

  it('id 穩定：同一份資料呼叫兩次相同，且彼此不重複', () => {
    const ids = (g = groupReport(real)) => g.units.map((u) => u.id)
    expect(ids()).toEqual(ids())
    expect(new Set(ids()).size).toBe(ids().length)
  })
})

describe('plainText', () => {
  it('攤平 strong／link／code，只留文字', () => {
    expect(plainText([{ type: 'strong', children: [{ type: 'link', href: 'h', children: t('標題') }] }, { type: 'code', text: 'X9' }, ...t('尾')])).toBe('標題X9尾')
  })
})
