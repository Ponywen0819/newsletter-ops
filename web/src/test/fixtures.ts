import type { Report } from '../types'

export const UID_A = '0123456789abcdef'
export const UID_B = '1111111111111111'
export const UID_C = '2222222222222222'

/** 與 src/report_data.py selftest 的 Markdown 解析結果同形：巢狀清單、mark 掛在最外層最後一項、獨立的 mark。 */
export const report: Report = {
  title: '每日晨間簡報',
  subject: '測試頭條',
  headline: '某事發生，見 來源。',
  blocks: [
    { type: 'title', title: '每日晨間簡報', date: '2026-09-28' },
    {
      type: 'callout',
      inline: [
        { type: 'strong', children: [{ type: 'text', text: '今日頭條：' }] },
        { type: 'text', text: ' 某事發生，見 ' },
        { type: 'link', href: 'https://a.example/x?a=1&b=2', children: [{ type: 'text', text: '來源' }] },
        { type: 'text', text: '。' },
      ],
    },
    { type: 'heading', inline: [{ type: 'text', text: '科技與 AI' }] },
    {
      type: 'paragraph',
      inline: [
        {
          type: 'strong',
          children: [{ type: 'link', href: 'https://a.example/1', children: [{ type: 'text', text: '重點 <標題>' }] }],
        },
      ],
    },
    {
      type: 'list',
      items: [
        {
          inline: [{ type: 'text', text: '發生什麼：' }, { type: 'code', text: 'X900' }],
          children: [{ inline: [{ type: 'text', text: '細節' }] }],
        },
        { inline: [{ type: 'text', text: '背景：B' }], uids: [UID_A] },
      ],
    },
    { type: 'heading', inline: [{ type: 'text', text: '其餘收錄' }] },
    { type: 'list', items: [{ inline: [{ type: 'text', text: '其他' }], children: [{ inline: [{ type: 'text', text: '補充' }] }], uids: [UID_B] }] },
    { type: 'mark', uid: UID_C },
  ],
  sources: [
    { title: '來源', url: 'https://a.example/x?a=1&b=2' },
    { title: '重點 <標題>', url: 'https://a.example/1' },
  ],
}
