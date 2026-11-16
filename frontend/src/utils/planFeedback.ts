// 方案相关的统一提示：历史方案重新启用时弹一个蓝色说明框
import { ElMessageBox } from 'element-plus'

export interface ImportResult {
  message?: string
  activated?: { reactivated?: boolean; archived_count?: number; archived_titles?: string[] }
  shift?: { shift_days?: number; from_date?: string | null; to_date?: string | null }
}

/**
 * 导入的是历史方案（原状态 archived）→ 弹「已重新启用」说明框。
 * 返回 true 表示已弹过，调用方就不必再弹普通 toast。
 */
export function notifyReactivated(resp: ImportResult): boolean {
  if (!resp.activated?.reactivated) return false

  const rows: string[] = ['这份方案已从「历史方案」重新启用，现在是进行中。']
  if (resp.shift?.shift_days) {
    rows.push(`日期已过，已整体顺延 ${resp.shift.shift_days} 天，从 ${resp.shift.to_date} 起排。`)
  }
  if (resp.activated.archived_count) {
    rows.push(`原来进行中的 ${resp.activated.archived_count} 份方案已自动转到「历史方案」。`)
  }
  rows.push('日程已按新日期生成任务，可以直接打卡。')

  const html = `
    <div style="background:#eef7ff;border:1px solid #cfe6fb;border-radius:10px;padding:10px 12px;color:#2f7bbf;">
      <div style="font-size:13px;font-weight:600;margin-bottom:6px;">↻ 已重新启用</div>
      ${rows.map((t) => `<p style="margin:0 0 4px;font-size:12.5px;line-height:1.65;">${t}</p>`).join('')}
    </div>`

  ElMessageBox.alert(html, '已重新启用', {
    dangerouslyUseHTMLString: true,
    confirmButtonText: '知道了',
    customClass: 'reactivate-msgbox',
  }).catch(() => {})
  return true
}
