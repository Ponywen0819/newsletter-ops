import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { mockFetch } from '../test/fetchMock'
import type { MetricsPayload, OpsConfig, OpsStatus } from '../types'

const NOW = new Date().toISOString()

const status = (over: Partial<OpsStatus> = {}): OpsStatus => ({
  worker: {
    online: true,
    scheduled: true,
    run_at: '08:00',
    next_run_at: '2026-09-29T00:00:00+00:00',
    state: 'idle',
    current: null,
  },
  job: null,
  log: [],
  last_run: {
    date: '2026-09-28',
    trigger: 'schedule',
    started_at: '2026-09-28T00:00:00+00:00',
    finished_at: '2026-09-28T00:10:00+00:00',
    exit_code: 5,
    step: 'write',
  },
  ...over,
})

function open(path: string, routes: Parameters<typeof mockFetch>[0], local = true) {
  const net = mockFetch({ 'GET /api/session': () => ({ json: { local } }), ...routes })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
  return net
}

const opsCalls = (net: ReturnType<typeof mockFetch>) => net.calls.filter((c) => c.path.startsWith('/api/ops'))

afterEach(() => vi.restoreAllMocks())

describe('/ops', () => {
  it('非本機：找不到頁面、沒有導覽連結、不碰 /api/ops', async () => {
    const net = open('/ops', {}, false)
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '維運' })).toBeNull()
    expect(opsCalls(net)).toEqual([])
  })

  it('本機：排程、上次結果；預設從上次失敗的那步重跑', async () => {
    const net = open('/ops', {
      'GET /api/ops': () => ({ json: status() }),
      'POST /api/ops/request': () => ({ json: { started_at: NOW, id: 'r1' } }),
    })
    expect(await screen.findByText('運作中')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '維運' })).toBeInTheDocument()
    expect(document.title).toBe('維運')
    expect(screen.getByText(/在「撰寫報告」失敗（exit 5）：Claude 授權失效/)).toBeInTheDocument()
    const select = screen.getByLabelText('從哪一步開始')
    expect(select).toHaveValue('write')

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await userEvent.click(screen.getByRole('button', { name: '執行每日流程' }))
    expect(confirm.mock.calls[0]![0]).toContain('會用到 Claude 訂閱額度')
    expect(await screen.findByText('已開始')).toBeInTheDocument()
    expect(opsCalls(net).find((c) => c.method === 'POST')!.body).toEqual({ kind: 'run', from: 'write' })

    // 改從排版開始：不重寫報告，確認文字也不同
    await userEvent.selectOptions(select, 'render')
    await userEvent.click(screen.getByRole('button', { name: '執行每日流程' }))
    expect(confirm.mock.calls[1]![0]).toContain('今天已經寫好的報告')
    await waitFor(() => expect(opsCalls(net).filter((c) => c.method === 'POST')).toHaveLength(2))
    expect(opsCalls(net).filter((c) => c.method === 'POST')[1]!.body).toEqual({ kind: 'run', from: 'render' })
  })

  it('取消確認就不送；重建知識圖譜與試抓來源各自的 kind', async () => {
    const net = open('/ops', {
      'GET /api/ops': () => ({ json: status({ last_run: null }) }),
      'POST /api/ops/request': () => ({ json: { started_at: NOW, id: 'r' } }),
    })
    await screen.findByText('還沒有執行紀錄。')
    expect(screen.getByLabelText('從哪一步開始')).toHaveValue('collect')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await userEvent.click(screen.getByRole('button', { name: '重建知識圖譜' }))
    expect(opsCalls(net).some((c) => c.method === 'POST')).toBe(false)
    confirm.mockReturnValue(true)
    await userEvent.click(screen.getByRole('button', { name: '重建知識圖譜' }))
    await userEvent.click(screen.getByRole('button', { name: '試抓來源' }))
    await waitFor(() => expect(opsCalls(net).filter((c) => c.method === 'POST')).toHaveLength(2))
    expect(opsCalls(net).filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([{ kind: 'kg-rebuild' }, { kind: 'sources-check' }])
  })

  it('連不上 worker：按鈕都不能按，上次的紀錄照樣顯示', async () => {
    open('/ops', { 'GET /api/ops': () => ({ json: status({ worker: { online: false } }) }) })
    expect(await screen.findByText('連不上')).toBeInTheDocument()
    expect(screen.getByText(/在「撰寫報告」失敗/)).toBeInTheDocument()
    for (const name of ['執行每日流程', '試抓來源', '重建知識圖譜']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
  })

  it('送出被拒：顯示伺服器的原因', async () => {
    open('/ops', {
      'GET /api/ops': () => ({ json: status() }),
      'POST /api/ops/request': () => ({ status: 409, json: { error: '已有工作在跑，等它結束再試' } }),
    })
    await screen.findByText('運作中')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await userEvent.click(screen.getByRole('button', { name: '試抓來源' }))
    expect(await screen.findByText('已有工作在跑，等它結束再試')).toBeInTheDocument()
  })

  it('執行中：按鈕停用、顯示進度與 log', async () => {
    open('/ops', {
      'GET /api/ops': () => ({
        json: status({
          worker: { ...status().worker, state: 'running', current: { kind: 'kg-rebuild', trigger: 'manual', started_at: NOW } },
          job: { id: 'j', kind: 'kg-rebuild', requested_at: NOW, started_at: NOW, finished_at: null, exit_code: null },
          log: ['=== 開始 ===', '[kg] 2026-09-01 {}'],
        }),
      }),
    })
    expect(await screen.findByText(/執行中：重建知識圖譜（手動/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '執行每日流程' })).toBeDisabled()
    const job = within(screen.getByRole('region', { name: '最近一次手動執行' }))
    expect(job.getByText('執行中…')).toBeInTheDocument()
    expect(job.getByLabelText('執行輸出（最後幾行）')).toHaveTextContent('[kg] 2026-09-01')
  })
})

describe('/ops/metrics', () => {
  const metrics = (over: Partial<MetricsPayload> = {}): MetricsPayload => ({
    days: 14,
    all: false,
    debug: true,
    runs: [
      {
        date: '2026-09-28',
        time: '08:00',
        label: 'prod',
        trigger: 'schedule',
        result: { exit_code: 0, step: null },
        seconds: { fetch: 10, claude: 200, pipeline: 425 },
        tokens: { input: 123456, output: 7890 },
        turns: 12,
        usd: 0.75,
        items: 30,
      },
      {
        date: '2026-09-27',
        time: '08:30',
        label: 'prod',
        trigger: 'retry',
        result: { exit_code: 4, step: 'write' },
        seconds: { pipeline: 5 },
        tokens: null,
        turns: null,
        usd: null,
        items: null,
      },
    ],
    ...over,
  })

  it('表格、合計；切換期間與包含測試會重新載入', async () => {
    const net = open('/ops/metrics', {
      'GET /api/ops/metrics?days=14': () => ({ json: metrics() }),
      'GET /api/ops/metrics?days=14&all=1': () => ({ json: metrics({ all: true, runs: [{ ...metrics().runs[0]!, label: 'ops' }] }) }),
      'GET /api/ops/metrics?days=30&all=1': () => ({ json: metrics({ days: 30, all: true, runs: [] }) }),
    })
    const table = await screen.findByRole('table')
    expect(screen.getByText(/共 2 次，費用合計 0.75 USD/)).toBeInTheDocument()
    const rows = within(table).getAllByRole('row')
    expect(rows[1]).toHaveTextContent('2026-09-28')
    expect(rows[1]).toHaveTextContent('成功')
    expect(rows[1]).toHaveTextContent('7m05s')
    expect(rows[1]).toHaveTextContent('123,456')
    expect(rows[2]).toHaveTextContent('撰寫報告 exit 4')
    expect(rows[2]).toHaveTextContent('自動重跑')
    expect(within(table).queryByRole('columnheader', { name: '標籤' })).toBeNull()

    await userEvent.click(screen.getByLabelText('包含測試與維運工作'))
    expect(await screen.findByRole('columnheader', { name: '標籤' })).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('期間'), '30')
    expect(await screen.findByText('這段期間沒有執行紀錄。')).toBeInTheDocument()
    expect(opsCalls(net).map((c) => c.path)).toEqual([
      '/api/ops/metrics?days=14',
      '/api/ops/metrics?days=14&all=1',
      '/api/ops/metrics?days=30&all=1',
    ])
  })

  it('debug 沒開：提醒不會有新紀錄', async () => {
    open('/ops/metrics', { 'GET /api/ops/metrics?days=14': () => ({ json: metrics({ debug: false, runs: [] }) }) })
    expect(await screen.findByText(/debug 沒有開/)).toBeInTheDocument()
  })
})

describe('/ops/config', () => {
  const config: OpsConfig = {
    config: { debug: true, timezone: 'Asia/Taipei' },
    interests: '# 興趣\n- 半導體',
    sources: [
      { file: 'a.json', error: '讀不到或不是合法的 JSON' },
      {
        file: 'ai.json',
        topic: 'ai-industry',
        label: 'AI 產業',
        defaults: { weight: 1.2 },
        feeds: [
          { name: 'OpenAI News', type: 'rss', url: 'https://openai.com/news/rss.xml' },
          { name: '怪的', type: 'rss', url: 'javascript:alert(1)', note: '不該變成連結' },
        ],
      },
    ],
  }

  it('來源、興趣、主設定；只有 http(s) 網址是連結', async () => {
    open('/ops/config', { 'GET /api/ops/config': () => ({ json: config }) })
    expect(await screen.findByRole('heading', { name: /AI 產業/ })).toBeInTheDocument()
    expect(screen.getByText(/2 個主題、2 個來源/)).toBeInTheDocument()
    expect(screen.getByText('讀不到或不是合法的 JSON')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://openai.com/news/rss.xml' })).toHaveAttribute('target', '_blank')
    expect(screen.queryByRole('link', { name: 'javascript:alert(1)' })).toBeNull()
    expect(screen.getByText('javascript:alert(1)')).toBeInTheDocument()
    expect(screen.getByText(/weight = 1.2/)).toBeInTheDocument()
    expect(screen.getByText(/# 興趣/)).toBeInTheDocument()
    expect(screen.getByText(/"timezone": "Asia\/Taipei"/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '設定' })).toHaveAttribute('aria-current', 'page')
  })
})
