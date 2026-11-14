// 後端 JSON 的型別。結構定義在 shared 的 report_data.py（晨報）、auth_store.py（授權狀態）與 apps/web-server 的 server.py（API）。

export type Mark = '+' | '-' | ''

export type InlineNode =
  | { type: 'text'; text: string }
  | { type: 'strong'; children: InlineNode[] }
  | { type: 'link'; href: string; children: InlineNode[] }
  | { type: 'code'; text: string }

export interface ListItem {
  inline: InlineNode[]
  children?: ListItem[]
  /** 掛在這個項目底下的 mark 註解（有用／沒用 的對象） */
  uids?: string[]
}

export type Block =
  | { type: 'title'; title: string; date: string | null }
  | { type: 'heading'; inline: InlineNode[] }
  | { type: 'callout'; inline: InlineNode[] }
  | { type: 'paragraph'; inline: InlineNode[] }
  /** 缺少時當作可投票（舊版後端沒有這個欄位） */
  | { type: 'list'; items: ListItem[]; votable?: boolean }
  | { type: 'mark'; uid: string; votable?: boolean }

export interface Report {
  title: string
  subject: string
  headline: string
  blocks: Block[]
  sources: { title: string; url: string }[]
}

/** GET /api/feedback/<uid>：email 連結的確認頁要顯示的資料 */
export interface FeedbackTarget {
  uid: string
  /** 含這則的最新一份晨報 */
  date: string
  /** 沒有 curated 資料時是空字串 */
  title: string
  mark: Mark
}

/** uid → 目前的標記；沒標或已取消的不在裡面 */
export type Marks = Record<string, Mark>

/** 事實判定：wrong＝事實有誤（不再當背景）、trusted＝確認可信（不被自動判定失效） */
export type KgFlag = 'wrong' | 'trusted'

/** uid → 使用者的判定；沒標的不在裡面。晨報頁只會有 wrong，知識圖譜兩種都有 */
export type Flags = Record<string, KgFlag>

/** 前一份（更舊）／下一份（更新）的日期；沒有是 null。舊的離線副本沒有這兩欄 */
export interface Adjacent {
  prev?: string | null
  next?: string | null
}

export interface ReportPayload extends Adjacent {
  date: string
  report: Report
  marks: Marks
  /** 舊的離線副本沒有這欄 */
  flags?: Flags
}

export interface TodayPayload extends Adjacent {
  date: string
  /** 當日晨報還沒產出時是 null */
  report: Report | null
  marks: Marks
  flags?: Flags
  /** 最新一份晨報的日期；一份都沒有時是 null */
  latest: string | null
  /** 當天的執行紀錄（worker 的 state/last_run.json）；今天還沒跑是 null。舊的副本沒有這欄 */
  run?: RunRecord | null
  /** 當日晨報還沒產出、但有舊的時，最新一份的整份資料（同 /api/reports/<date>）。舊的副本沒有這欄 */
  fallback?: ReportPayload
}

export interface RunRecord {
  trigger: string
  started_at: string
  /** 還在跑時是 null */
  finished_at: string | null
  exit_code: number | null
  /** 失敗的那一步：collect / write / render / send */
  step: string | null
}

export interface ReportSummary {
  date: string
  headline: string
}

export interface StatRow {
  name: string
  pos: number
  neg: number
}

/** GET /api/feedback-stats：依來源／主題／關鍵字的正負面則數，則數多的在前 */
export interface FeedbackStats {
  total: number
  groups: { source: StatRow[]; topic: StatRow[]; keyword: StatRow[] }
}

/** GET /api/search?q=：含 q 的行（純文字），新到舊；超過上限時 truncated */
export interface SearchResult {
  q: string
  results: { date: string; text: string }[]
  truncated: boolean
}

export interface Session {
  /** 這個請求是不是從本機來（經 Tunnel 進來的不算）；只有本機能看到「Claude 授權」 */
  local: boolean
}

export type AuthStatus =
  | { state: 'missing' }
  | {
      state: 'ok' | 'expiring' | 'expired'
      /** token 的尾 4 碼；完整的 token 不會回傳 */
      tail: string
      saved_at: string
      expires_at: string
      days_left: number
    }

/** 最近一次驗證的結果（GET /api/auth-health，所有人都讀得到，所以不含驗證訊息） */
export interface AuthHealth {
  /** state 的意思見 apps/web-server/README.md */
  state: 'ok' | 'expiring' | 'missing' | 'invalid' | 'unknown'
  kind: string
  /** 最近一次驗證的時間（ISO）；還沒驗證過是 null */
  checked_at: string | null
}

export interface AuthResult {
  message: string
  status: AuthStatus
}

// ---- 知識圖譜（apps/web-server 的 kg.py；事件狀態的規則在 shared 的 kg_store.py）----

export type KgStatus = 'active' | 'disputed' | 'invalid'
/** src 是較新的事件：same＝同一件事、follow＝src 是 dst 的後續、contradict＝說法衝突、retract＝src 撤回／否認 dst */
export type KgKind = 'same' | 'follow' | 'contradict' | 'retract'
export type KgEntityType = 'org' | 'person' | 'product' | 'place' | 'other'

export interface KgEntityRef {
  /** 正規化後的名稱（kg_store.norm），網址用這個 */
  key: string
  name: string
  type: KgEntityType
}

/** 從新聞萃取出的一個事件。summary、quote、title 是外部網頁來的文字，只當純文字顯示 */
export interface KgEvent {
  id: number
  uid: string
  date: string
  summary: string
  /** 原文逐字引句（萃取時的證據） */
  quote: string
  title: string
  url: string
  source: string
  status: KgStatus
  /** 報導這件事的來源數（same 連到的也算） */
  sources: number
  /** 收錄這則的最新一份晨報；沒寫進晨報是 null（這時不能標「事實有誤」） */
  report: string | null
  entities: KgEntityRef[]
}

export interface KgLink {
  src: number
  dst: number
  kind: KgKind
  /** contradict／retract 的證據引句；其他是空字串 */
  quote: string
  date: string
}

export interface KgGraph {
  /** 舊到新 */
  events: KgEvent[]
  /** 只含兩端都在 events 裡的 */
  links: KgLink[]
  /** 圖裡被標了「事實有誤」或「確認可信」的則 */
  flags: Flags
  /** 超過上限被截斷 */
  truncated: boolean
}

export interface KgItem {
  uid: string
  date: string
  title: string
  url: string
  source: string
  report: string | null
}

/** GET /api/kg/item/<uid,…>?depth=：以這幾則的事件為中心往外 depth 層 */
export interface KgItemPayload extends KgGraph {
  uids: string[]
  items: KgItem[]
  depth: 1 | 2
  /** 這幾則自己的事件 id */
  seeds: number[]
}

/** GET /api/kg/entity/<key>?days=：提到這個實體的事件 */
export interface KgEntityPayload extends KgGraph {
  entity: KgEntityRef & { total: number }
  days: number
  since: string
  /** 在這些事件裡常一起出現的實體 */
  related: (KgEntityRef & { count: number })[]
}

/** GET /api/kg/entities?q=：q 空白＝近 30 天最常出現的；否則是搜尋結果 */
export interface KgEntities {
  q: string
  /** 還沒建過知識圖譜是 false */
  available: boolean
  since: string | null
  entities: (KgEntityRef & { count: number; last: string })[]
}

/* ---------- /ops 維運（只限本機） ---------- */

export type OpsKind = 'run' | 'kg-rebuild' | 'sources-check' | 'rewrite' // rewrite：測試區由 devtools compare 發起，頁面不提供按鈕
export type RunStep = 'collect' | 'write' | 'render' | 'send'

export interface OpsCurrent {
  kind: OpsKind
  trigger: string
  started_at: string
}

/** worker 的 GET /status；連不上時只有 online: false */
export interface OpsWorker {
  online: boolean
  scheduled?: boolean
  /** 每日執行時間 HH:MM */
  run_at?: string
  /** 排程關閉時是 null */
  next_run_at?: string | null
  state?: 'idle' | 'running'
  current?: OpsCurrent | null
}

/** 最近一次手動執行（worker 的 state/last_job.json） */
export interface OpsJob {
  id: string
  kind: OpsKind
  /** 只有 run 有 */
  from?: RunStep
  requested_at: string
  started_at?: string
  /** 還在跑時是 null */
  finished_at?: string | null
  exit_code?: number | null
}

export interface OpsStatus {
  worker: OpsWorker
  job: OpsJob | null
  /** job 的 log 最後幾行 */
  log: string[]
  last_run: (RunRecord & { date: string }) | null
}

export interface MetricsRun {
  date: string
  time: string
  label: string
  trigger: string | null
  /** 沒有 pipeline 紀錄（手動跑單一步驟）是 null */
  result: { exit_code: number; step: string | null } | null
  /** 階段 → 秒數（fetch、curate、search、enrich、claude、render、pipeline） */
  seconds: Record<string, number>
  /** input 含快取讀寫 */
  tokens: { input: number; output: number } | null
  turns: number | null
  usd: number | null
  items: number | null
}

export interface MetricsPayload {
  days: number
  all: boolean
  /** config.json 的 debug；關著時不會有新的紀錄 */
  debug: boolean
  /** 新到舊 */
  runs: MetricsRun[]
}

export interface SourceFeed {
  name: string
  type: string
  url?: string
  note?: string
  [key: string]: unknown
}

export type SourceFile =
  | { file: string; error: string }
  | { file: string; topic?: string; label?: string; defaults: Record<string, unknown>; feeds: SourceFeed[] }

export interface OpsConfig {
  config: Record<string, unknown> | null
  sources: SourceFile[]
  interests: string | null
}
