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

/** 前一份（更舊）／下一份（更新）的日期；沒有是 null。舊的離線副本沒有這兩欄 */
export interface Adjacent {
  prev?: string | null
  next?: string | null
}

export interface ReportPayload extends Adjacent {
  date: string
  report: Report
  marks: Marks
}

export interface TodayPayload extends Adjacent {
  date: string
  /** 當日晨報還沒產出時是 null */
  report: Report | null
  marks: Marks
  /** 最新一份晨報的日期；一份都沒有時是 null */
  latest: string | null
  /** 當天的執行紀錄（worker 的 state/last_run.json）；今天還沒跑是 null。舊的副本沒有這欄 */
  run?: RunRecord | null
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
