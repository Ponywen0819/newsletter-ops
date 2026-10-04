// 後端 JSON 的型別。結構定義在 shared 的 report_data.py（晨報）、auth_store.py（授權狀態）與 src/web.py（API）。

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
  /**
   * votable：這張清單（或獨立 mark）要不要放有用／沒用。規則在後端 report_data.py，網頁與 email 都只讀這個旗標。
   * 舊版後端沒有這個欄位：缺少時當作可投票——寧可多一組按鈕，也不要讓整頁的投票消失。
   */
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

export interface ReportPayload {
  date: string
  report: Report
  marks: Marks
}

export interface TodayPayload {
  date: string
  /** 當日晨報還沒產出時是 null */
  report: Report | null
  marks: Marks
  /** 最新一份晨報的日期；一份都沒有時是 null */
  latest: string | null
}

export interface ReportSummary {
  date: string
  headline: string
}

export interface Session {
  /** 這個請求是不是從本機來（經 Tunnel 進來的不算）；只有本機能看到「Claude 授權」 */
  local: boolean
}

export type AuthStatus =
  | { state: 'missing'; env_token: boolean }
  | {
      state: 'ok' | 'expiring' | 'expired'
      env_token: boolean
      /** token 的尾 4 碼；完整的 token 不會回傳 */
      tail: string
      saved_at: string
      expires_at: string
      days_left: number
    }

export interface AuthResult {
  message: string
  status: AuthStatus
}
