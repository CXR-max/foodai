// 体检报告与个人分析：日程页小卡 + /m/analysis 详情页共用
import { get, post, del } from './http'

export interface CheckupField {
  key: string
  name: string
  unit: string
  value: number
  value_text: string
  ref_text: string
  status: 'normal' | 'high' | 'low'
  high_bad: boolean
}

export interface CheckupGroup {
  key: string
  name: string
  items: CheckupField[]
}

export interface CheckupReport {
  id: number
  check_date: string
  source: 'manual' | 'photo' | 'pdf'
  hospital?: string | null
  note?: string | null
  file_path?: string | null
  metric_count: number
  attention_count: number
}

export interface AnalysisSection {
  text: string
  chips: string[]
}

export interface CompareRow {
  key: string
  name: string
  unit: string
  baseline: string | null
  now: string | null
  delta: string | null
  delta_raw: number | null
  status: 'normal' | 'high' | 'low'
}

export interface CheckupAnalysis {
  has_report: boolean
  type: string | null
  headline: string
  chips: string[]
  report: CheckupReport | null
  reports: CheckupReport[]
  groups: CheckupGroup[]
  metric_count: number
  attention_count: number
  baseline_date?: string
  sections: {
    now?: AnalysisSection
    cause?: AnalysisSection
    forecast?: AnalysisSection
    actions?: AnalysisSection
  }
  compare: CompareRow[]
  profile: { height_cm: number | null; weight_kg: number | null; gender: string | null }
}

export interface CatalogField {
  key: string
  name: string
  unit: string
  decimals: number
  ref_text: string
}

export const getAnalysis = () => get<CheckupAnalysis>('/checkups/analysis')

export const getCatalog = () =>
  get<{ groups: { key: string; name: string; items: CatalogField[] }[] }>('/checkups/catalog')

export const listCheckups = () =>
  get<{ items: (CheckupReport & { groups: CheckupGroup[] })[] }>('/checkups')

export const saveCheckup = (data: {
  check_date?: string
  source?: 'manual' | 'photo' | 'pdf'
  hospital?: string
  file_path?: string
  note?: string
  metrics: Record<string, number | string | null>
}) => post<CheckupReport>('/checkups', data)

export const deleteCheckup = (id: number) => del<{ ok: boolean }>(`/checkups/${id}`)

/** 拍照 / PDF 上传后由后端识别，返回预填结果（用户核对后才入库） */
export interface ScanItem {
  key: string
  name: string
  value: number
  unit: string
  raw_name: string
  raw_value: number
  raw_unit: string
  converted: boolean
  convert_note: string
}

export interface ScanResult {
  is_report: boolean
  report_date: string
  hospital: string
  file_path: string
  filename: string
  source: 'photo' | 'pdf'
  items: ScanItem[]
  unmatched: { name: string; value: number; unit: string }[]
  warnings: string[]
  metric_count: number
}

export const scanCheckup = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  // 视觉识别单页 10~20 秒，扫描版 PDF 两页更久，单独放宽超时
  return post<ScanResult>('/checkups/scan', fd, { timeout: 120000 })
}
