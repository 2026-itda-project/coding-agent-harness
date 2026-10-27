// detail 형식: {code, message} | 문자열(빈 입력) | 배열(FastAPI 요청 스키마 422)
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    const message = typeof detail?.message === 'string' ? detail.message : typeof detail === 'string' ? detail : null
    throw new Error(message ?? `요청 실패 (${response.status})`)
  }
  return await response.json() as T
}

export function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
}

export function get<T>(url: string): Promise<T> {
  return request<T>(url)
}
