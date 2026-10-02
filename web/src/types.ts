// 後端 JSON 的型別。結構定義在 src/report_data.py（晨報）與 src/web.py（API）、src/auth_store.py（授權狀態）。

export type Mark = '+' | '-' | ''

export type InlineNode =
  | { type: 'text'; text: string }
  | { type: 'strong'; children: InlineNode[] }
  | { type: 'link'; href: string; children: InlineNode[] }
  | { type: 'code'; text: string }

export interface ListItem {
  inline: InlineNode[]
  children?: ListItem[]
  /** 掛在這個項目底下的 mark 註解（👍／👎 的對象） */
  uids?: string[]
}

export type Block =
  | { type: 'title'; title: string; date: string | null }
  | { type: 'heading'; inline: InlineNode[] }
  | { type: 'callout'; inline: InlineNode[] }
  | { type: 'paragraph'; inline: InlineNode[] }
  | { type: 'list'; items: ListItem[] }
  | { type: 'mark'; uid: string }

export interface Report {
  title: string
  subject: string
  headline: string
  blocks: Block[]
  sources: { title: string; url: string }[]
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
