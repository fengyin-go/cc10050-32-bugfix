/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: unknown
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow | null
  receipt?: Receipt | null
  [key: string]: unknown
}

export type ReceiptSample = {
  id: number
  来源引用?: string
  站点名称?: string
  模板版本?: string
  解析价格?: number | string
  生效价格?: number | string
  status?: string
}

export type AffectedBaseline = {
  id: number
  基线编号?: string
  商品标识?: string
  归一规格?: string
  站点名称?: string
  status?: string
  基线价格?: number | string
  消费模板版本?: string
  sample_ids?: number[]
  sample_refs?: string[]
  samples?: ReceiptSample[]
}

export type Receipt = {
  id: number
  receipt_id?: number
  回执编号?: string
  kind?: string
  类型?: string
  message?: string
  created_at?: string
  模板名称?: string
  站点名称?: string
  from_version?: string
  to_version?: string
  baseline_id?: number | null
  affected_count?: number
  affected_baseline_ids?: number[]
  affected_baselines?: AffectedBaseline[]
  causing_samples?: ReceiptSample[]
  sample_ids?: number[]
  [key: string]: unknown
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
