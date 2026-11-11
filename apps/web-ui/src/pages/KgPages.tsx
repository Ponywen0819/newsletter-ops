import { type FormEvent, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { EXTERNAL } from '../components/Inline'
import { entityPath, KgView, TYPE_LABEL } from '../components/KgView'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'

// 知識圖譜的三頁。只在線上能用（不進離線副本），整組在 App.tsx 裡 lazy 載入，不加重晨報本身。

const DAYS = [30, 90, 365] as const

/** 讀取失敗：404 是 KG 裡沒有（還沒建、還沒處理到），其他多半是連不上 */
function Failed({ error, missing }: { error: Error; missing: string }) {
  if (error instanceof ApiError && error.status === 404) {
    return (
      <Notice title="知識圖譜裡沒有資料">
        {missing}知識圖譜在每天寫晨報前更新，也可以在主機上用 <code>newsletter-kg --rebuild</code> 從過去的新聞重建。
      </Notice>
    )
  }
  return <Notice title="讀取失敗">{error instanceof ApiError ? error.message : '連不上伺服器；知識圖譜需要連線才能看。'}</Notice>
}

/** 重新載入時（換範圍、標了事實有誤）先留著舊的圖，不閃成「載入中」，選中的事件也不會跑掉。 */
function useLatest<T>(data: T | undefined): T | undefined {
  const [latest, setLatest] = useState(data)
  if (data !== undefined && data !== latest) setLatest(data)
  return data ?? latest
}

function Choice<T extends string | number>({ label, options, value, onChange }: { label: string; options: { value: T; text: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="kg-toolbar" role="group" aria-label={label}>
      <span>{label}</span>
      {options.map((o) => (
        <button key={o.value} type="button" className="fb-btn" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.text}
        </button>
      ))}
    </div>
  )
}

/** /kg：找實體。沒輸入時列近 30 天最常出現的。 */
export function KgIndexPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const { data, error } = useFetch(() => api.kg.entities(q), [q, useRecovered()])
  useDocumentTitle(q ? `搜尋「${q}」— 知識圖譜` : '知識圖譜')
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const next = String(new FormData(e.currentTarget).get('q') ?? '').trim()
    setParams(next ? { q: next } : {})
  }
  return (
    <>
      <h1>知識圖譜</h1>
      <p className="kg-lead">
        每天的新聞會被拆成「誰做了什麼」的事件，並和先前的報導比對：後續發展、同一件事、說法矛盾、撤回或否認。搜尋公司、人物或產品看它的時間軸；在晨報裡按「脈絡」看單則新聞的來龍去脈。
      </p>
      <form role="search" className="kg-search" onSubmit={submit}>
        <input key={q} type="search" name="q" defaultValue={q} placeholder="搜尋公司、人物、產品…" aria-label="搜尋實體" maxLength={100} />
      </form>
      {error ? (
        <Failed error={error} missing="" />
      ) : !data ? (
        <Loading />
      ) : !data.available ? (
        <p className="kg-note">知識圖譜還沒建立。每天寫晨報前會自動更新，第一次可以在主機上跑 newsletter-kg --rebuild。</p>
      ) : (
        <section>
          <h2>{q ? `「${q}」的搜尋結果` : `近 30 天最常出現`}</h2>
          {data.entities.length === 0 ? (
            <p className="kg-note">{q ? '沒有符合的實體。' : '近 30 天沒有新的事件。'}</p>
          ) : (
            <ul className="kg-entity-list">
              {data.entities.map((e) => (
                <li key={e.key}>
                  <Link to={entityPath(e.key)}>{e.name}</Link>
                  <span>
                    {TYPE_LABEL[e.type]} · {e.count} 個事件 · 最近 {e.last}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  )
}

/** /kg/item/<uid,…>：單則新聞的脈絡（併了多篇的新聞會有好幾個 uid）。 */
export function KgItemPage() {
  const { uids = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const depth = params.get('depth') === '2' ? 2 : 1
  const [version, setVersion] = useState(0)
  const { data: fresh, error } = useFetch(() => api.kg.item(uids.split(','), depth), [uids, depth, version, useRecovered()])
  const latest = useLatest(fresh)
  const data = latest?.uids.join(',') === [...new Set(uids.split(','))].join(',') ? latest : undefined // 換一則時不要先顯示上一則
  const first = data?.items[0]
  useDocumentTitle(first ? `脈絡：${first.title}` : '新聞脈絡')
  if (error) return <Failed error={error} missing="這則新聞還沒被萃取成事件。" />
  if (!data) return <Loading />
  const report = data.items.find((i) => i.report)?.report
  return (
    <>
      <h1>新聞脈絡</h1>
      <ul className="kg-items">
        {data.items.map((item) => (
          <li key={item.uid}>
            <a href={item.url} {...EXTERNAL}>
              {item.title || item.url}
            </a>
            <span>
              {item.source} · {item.date}
            </span>
          </li>
        ))}
      </ul>
      {report && (
        <p className="kg-actions">
          <Link to={`/reports/${report}`}>回到 {report} 晨報</Link>
        </p>
      )}
      <Choice
        label="範圍"
        value={depth}
        options={[
          { value: 1, text: '直接相關' },
          { value: 2, text: '再往外一層' },
        ]}
        onChange={(d) => setParams(d === 2 ? { depth: '2' } : {})}
      />
      {data.events.length === 0 ? (
        <p className="kg-note">這則沒有萃取出具體事件（例如評論、分析文章），沒有脈絡可畫。</p>
      ) : (
        <>
          {data.links.length === 0 && <p className="kg-note">還沒找到和先前報導的關係；下面是這則本身的事件。</p>}
          <KgView key={uids} graph={data} seeds={data.seeds} onFlagged={() => setVersion((v) => v + 1)} />
        </>
      )}
    </>
  )
}

/** /kg/entity/<key>：一個實體的事件時間軸。 */
export function KgEntityPage() {
  const { key = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const asked = Number(params.get('days'))
  const days = (DAYS as readonly number[]).includes(asked) ? asked : 90
  const [version, setVersion] = useState(0)
  const { data: fresh, error } = useFetch(() => api.kg.entity(key, days), [key, days, version, useRecovered()])
  const data = useLatest(fresh)
  const current = data?.entity.key === key ? data : undefined // 換實體時不要先顯示上一個
  useDocumentTitle(current ? `${current.entity.name} — 知識圖譜` : '知識圖譜')
  if (error) return <Failed error={error} missing="找不到這個實體。" />
  if (!current) return <Loading />
  const { entity } = current
  return (
    <>
      <p className="kg-crumb">
        <Link to="/kg">← 知識圖譜</Link>
      </p>
      <h1>{entity.name}</h1>
      <p className="kg-lead">
        {TYPE_LABEL[entity.type]} · 近 {current.days} 天 {current.events.length} 個事件（全部 {entity.total} 個）
      </p>
      <Choice
        label="期間"
        value={days}
        options={DAYS.map((d) => ({ value: d, text: d === 365 ? '一年' : `${d} 天` }))}
        onChange={(d) => setParams(d === 90 ? {} : { days: String(d) })}
      />
      {current.events.length === 0 ? (
        <p className="kg-note">這段期間沒有提到它的事件，試試拉長期間。</p>
      ) : (
        <KgView key={key} graph={current} onFlagged={() => setVersion((v) => v + 1)} />
      )}
      {current.related.length > 0 && (
        <section>
          <h2>常一起出現</h2>
          <ul className="kg-chips">
            {current.related.map((r) => (
              <li key={r.key}>
                <Link to={entityPath(r.key)} title={TYPE_LABEL[r.type]}>
                  {r.name}
                </Link>
                <span className="kg-count">{r.count}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}
