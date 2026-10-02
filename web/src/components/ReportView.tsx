import { useState } from 'react'
import type { ListItem, Mark, Marks, Report } from '../types'
import { FeedbackButtons } from './FeedbackButtons'
import { Inline } from './Inline'

type OnMark = (uid: string, mark: Mark) => void

function Items({ items, marks, onMark }: { items: ListItem[]; marks: Marks; onMark: OnMark }) {
  return (
    <ul>
      {items.map((item, i) => (
        <li key={i}>
          <Inline nodes={item.inline} />
          {item.children && <Items items={item.children} marks={marks} onMark={onMark} />}
          {item.uids?.map((uid) => (
            <FeedbackButtons key={uid} uid={uid} mark={marks[uid] ?? ''} onChange={onMark} />
          ))}
        </li>
      ))}
    </ul>
  )
}

/** 晨報本體：版型沿用 email（render_email.py）的樣子，每則的 mark 位置換成 👍／👎。 */
export function ReportView({ report, marks, onMark }: { report: Report; marks: Marks; onMark: OnMark }) {
  return (
    <article className="report">
      {report.blocks.map((block, i) => {
        switch (block.type) {
          case 'title':
            return (
              <div key={i}>
                <h1>{block.title}</h1>
                {block.date && <p className="report-date">{block.date.replaceAll('-', '/')}</p>}
              </div>
            )
          case 'heading':
            return (
              <h2 key={i}>
                <Inline nodes={block.inline} />
              </h2>
            )
          case 'callout':
            return (
              <div key={i} className="callout">
                <Inline nodes={block.inline} />
              </div>
            )
          case 'paragraph':
            return (
              <p key={i}>
                <Inline nodes={block.inline} />
              </p>
            )
          case 'list':
            return <Items key={i} items={block.items} marks={marks} onMark={onMark} />
          case 'mark':
            return <FeedbackButtons key={i} uid={block.uid} mark={marks[block.uid] ?? ''} onChange={onMark} />
        }
      })}
      {report.sources.length > 0 && (
        <>
          <h2>資料來源</h2>
          <ol className="sources">
            {report.sources.map((source) => (
              <li key={source.url}>
                <a href={source.url}>{source.title}</a>
              </li>
            ))}
          </ol>
        </>
      )}
    </article>
  )
}

/** 晨報加上它自己的標記狀態：按了 👍／👎 以伺服器回傳的結果更新畫面，不必重新載入整頁。 */
export function ReportPanel({ report, marks: initial }: { report: Report; marks: Marks }) {
  const [marks, setMarks] = useState(initial)
  const onMark: OnMark = (uid, mark) => setMarks((current) => ({ ...current, [uid]: mark }))
  return <ReportView report={report} marks={marks} onMark={onMark} />
}
