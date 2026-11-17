/** 统一请求封装：拼后端地址、抛网络错误、给页脚留一句可读的说明。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

export type ActionResult<T = Record<string, unknown>> = {
  ok: boolean
  message: string
  entry?: T
  effects?: Record<string, unknown> | null
}

/**
 * 动作接口：HTTP 200 也可能是业务失败（ok=false），必须按 body 里的 ok 判断，
 * 否则页面会把「评审中不能发布」这种拦截当成成功，看起来像点了没反应。
 */
export async function postAction<T = Record<string, unknown>>(
  path: string,
  body: Record<string, unknown>,
): Promise<ActionResult<T>> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = (await response.json().catch(() => null)) as ActionResult<T> | null
  if (!response.ok) {
    const detail = payload?.message ?? `接口返回 ${response.status}`
    throw new Error(detail)
  }
  if (!payload) {
    throw new Error('接口返回内容无法解析')
  }
  return payload
}
