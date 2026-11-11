export type UserRole = 'employee' | 'approver' | 'admin'
export type TaskStatus = 'queued' | 'running' | 'awaiting_approval' | 'returned' | 'writing_back' | 'succeeded' | 'rejected' | 'failed' | 'cancelled'
export type ClaimCategory = '交通费' | '住宿费' | '业务招待费' | '办公用品' | '通讯费' | '培训会议费' | '软件服务费' | '出差餐补' | '其他'

export interface User { username: string; name: string; role: UserRole; department?: string }
export interface Claim {
  project_id: 'P001' | 'P002'
  category: ClaimCategory
  amount: string
  expense_date: string
  description: string
  confirmed: true
  parse_id?: string
  invoice_id?: string
  conflict_acknowledged?: boolean
  confirmation_note?: string
  department?: string
  file_id?: string
  invoice?: InvoiceFields
  details?: Record<string, unknown>
}

export interface InvoiceFields {
  invoice_no: string; invoice_code: string; invoice_date: string | null
  buyer: string; seller: string; total: string; item_name: string
}
export interface ParseSuggestion extends ParseSnapshot {
  mode: string
  suggested_amount: string | null
  suggested_category: ClaimCategory | null
  requires_confirmation: true
  message: string
}
export interface Citation { id: string; text: string; source: string; version: string }
export interface TaskEvent { at: string; status: TaskStatus; message: string }
export interface ToolLog { task_id?: string; stage?: string; endpoint?: string; error_code?: string; at: string; tool: string; method: string; request: unknown; success: boolean; response?: unknown; error?: string; duration_ms: number }
export interface Approval { reviewed_fields?: boolean; corrected_fields?: CorrectedFields | null; correction_reason?: string; at: string; actor: string; decision: 'approve' | 'reject' | 'return'; comment: string; manual_basis: string }
export interface TaskResult {
  decision?: 'approved' | 'rejected'
  reason?: string
  mode?: string
  error?: string
  error_code?: string
  manual_reasons?: string[]
  return_reason?: string
  initial_decision?: string
  initial_review?: { conclusion: string; reasons: string[]; citation_ids: string[]; source: string; model?: string; latency_ms?: number }
  invoice_check?: { status: string; reason: string; simulated: boolean }
  duplicate_check?: { duplicate: boolean; suspected: boolean; simulated: boolean }
  budget?: { project_id: string; available: string; currency: string; simulated: boolean }
  receipt?: { task_id: string; decision: string; reason: string; receipt_id: string; simulated: boolean }
}
export interface Task {
  id: string
  business_no?: string | null
  owner: string
  status: TaskStatus
  payload: Claim
  result: TaskResult
  citations: Citation[]
  logs: ToolLog[]
  events: TaskEvent[]
  approvals: Approval[]
  created_at: string
  finished_at: string | null
  manual: boolean
  submission_payload: Claim
  parse_snapshot: ParseSnapshot
  confirmations: unknown[]
  amendments: unknown[]
  failure_stage: string | null
  policy_version?: string
  revision?: number
  revisions?: unknown[]
  return_draft?: Record<string,unknown>
}

let credentials = ''
export function setCredentials(username: string, password: string) { credentials = btoa(`${username}:${password}`) }

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

function validationMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map(item => {
      if (!item || typeof item !== 'object') return ''
      const issue = item as { loc?: Array<string | number>; msg?: string }
      const field = issue.loc?.filter(part => part !== 'body').join('.')
      return [field, issue.msg].filter(Boolean).join('：')
    }).filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  return '请求未能完成，请检查输入后重试'
}

function statusMessage(status: number, detail: unknown): string {
  const message = validationMessage(detail)
  if (status === 401) return '账号或密码错误，请重新登录'
  if (status === 403) return `当前账号没有执行此操作的权限：${message}`
  if (status === 404) return message === '请求未能完成，请检查输入后重试' ? '请求的数据不存在或不可见' : message
  if (status === 409) return `任务状态已变化，请刷新后重试：${message}`
  if (status === 422) return `提交内容不符合要求：${message}`
  if (status >= 500) return '业务服务暂时不可用，请稍后重试；已填写内容不会丢失'
  return message
}

export async function request<T>(path: string, method = 'GET', body?: unknown, key?: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api' + path, {
      method, signal,
      headers: { 'Content-Type': 'application/json', Authorization: 'Basic ' + credentials, ...(key ? { 'Idempotency-Key': key } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    })
  } catch {
    throw new Error('无法连接业务服务，请确认后端已启动后重试；已填写内容不会丢失')
  }
  const text = await response.text()
  let data: unknown = null
  if (text) { try { data = JSON.parse(text) } catch { data = null } }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data
    throw new ApiError(statusMessage(response.status, detail), response.status)
  }
  return data as T
}

export interface AmountCandidate { raw: string; amount: string | null; valid: boolean; reason?: string }
export interface FieldConflict { field: string; reason: string }
export interface ParseSnapshot {
  mode: string
  parse_id?: string
  requires_manual_review: boolean
  fallback_event?: unknown
  amount_candidates?: AmountCandidate[]
  conflicts?: FieldConflict[]
}
export type CorrectedFields = Partial<Pick<Claim, 'project_id' | 'category' | 'amount' | 'expense_date' | 'description'>>

export interface InvoiceMeta {
  filename: string
  pages: number
  text_chars: number
  text_preview: string
  hints: { invoice_no?: string; expense_date?: string; invoice_title?: string }
  file_id?: string
  fields?: InvoiceFields
  missing_fields?: string[]
  read_method?: string
  recognition_error?: { code: string; message: string } | null
}
export interface InvoiceSuggestion extends ParseSuggestion {
  invoice_id: string
  file_id?: string
  invoice: InvoiceMeta
}

async function readResponse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let data: unknown = null
  if (text) { try { data = JSON.parse(text) } catch { data = null } }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data
    throw new ApiError(statusMessage(response.status, detail), response.status)
  }
  return data as T
}

/** 上传电子发票 PDF，返回与 /api/parse 同构的抽取建议（含 invoice_id 与发票元信息）。 */
export async function uploadInvoice(file: File): Promise<InvoiceSuggestion> {
  const form = new FormData()
  form.append('file', file)
  let response: Response
  try {
    response = await fetch('/api/invoices', {
      method: 'POST',
      headers: { Authorization: 'Basic ' + credentials },
      body: form
    })
  } catch {
    throw new Error('无法连接业务服务，请确认后端已启动后重试；已填写内容不会丢失')
  }
  return readResponse<InvoiceSuggestion>(response)
}
export interface ApprovalRequest {
  decision: 'approve' | 'reject' | 'return'
  comment: string
  manual_basis?: string
  corrected_fields?: CorrectedFields
  correction_reason?: string
  reviewed_fields?: boolean
}

export async function readFile(fileId: string): Promise<Blob> {
  const response = await fetch('/api/files/' + encodeURIComponent(fileId) + '/content', {
    headers: { Authorization: 'Basic ' + credentials }
  })
  if (!response.ok) { await readResponse(response); throw new Error('无法读取原件') }
  return response.blob()
}
