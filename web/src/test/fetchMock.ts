import { vi } from 'vitest'

type Reply = { status?: number; json: unknown }
type Handler = (body: unknown) => Reply | Promise<Reply>

/**
 * 換掉全域 fetch：routes 的 key 是 "METHOD /path"，沒列到的一律 404。
 * 測試走真正的 api.ts，只假造網路那一層。calls 記錄每次請求（含解析過的 JSON body）。
 */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const path = String(input)
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    calls.push({ method, path, body, headers: (init?.headers ?? {}) as Record<string, string> })
    const handler = routes[`${method} ${path}`]
    const { status = 200, json } = handler ? await handler(body) : { status: 404, json: { error: 'not found' } }
    return new Response(JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return { calls, fn }
}
