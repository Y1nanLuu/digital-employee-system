export interface User { username: string; name: string; role: string }
export interface Claim { project_id: string; category: string; amount: string; expense_date: string; description: string; confirmed: boolean }
export interface Task { id: string; owner: string; status: string; payload: Claim; result: Record<string, unknown>; citations: Array<{id:string;text:string;source:string;version:string}>; logs: unknown[]; events: Array<{at:string;status:string;message:string}>; approvals: unknown[]; created_at: string }
let credentials = ''
export function setCredentials(username: string, password: string) { credentials = btoa(`${username}:${password}`) }
export async function request<T>(path: string, method = 'GET', body?: unknown, key?: string): Promise<T> {
  const response = await fetch('/api' + path, { method, headers: { 'Content-Type': 'application/json', Authorization: 'Basic ' + credentials, ...(key ? {'Idempotency-Key':key} : {}) }, ...(body === undefined ? {} : {body: JSON.stringify(body)}) })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data))
  return data as T
}
