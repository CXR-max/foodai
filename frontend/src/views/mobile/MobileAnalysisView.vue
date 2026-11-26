<script setup lang="ts">
// 个人分析：体检报告 + 综合结论（现状/归因/预测/动作）+ 基线对比
// 数据全部来自后端 /api/checkups/analysis，阈值由个人身高体重体脂推，不用通用常量
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as checkupApi from '@/api/checkup'
import type { CheckupAnalysis, CatalogField } from '@/api/checkup'
import { assetUrl } from '@/api/http'

const router = useRouter()

const data = ref<CheckupAnalysis | null>(null)
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    data.value = await checkupApi.getAnalysis()
  } finally {
    loading.value = false
  }
}

// ---------------- 文案 ----------------
const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function dateText(d?: string | null) {
  if (!d) return '—'
  const dt = new Date(`${d}T00:00:00`)
  return `${dt.getMonth() + 1}月${dt.getDate()}日 ${weekday[dt.getDay()]}`
}
const SOURCE_LABEL: Record<string, string> = { manual: '手动录入', photo: '拍照导入', pdf: 'PDF 导入' }
const statusText = (s: string) => (s === 'high' ? '偏高' : s === 'low' ? '偏低' : '正常')

const SECTIONS = [
  { key: 'now', title: '现状', icon: 'pulse' },
  { key: 'cause', title: '归因', icon: 'search' },
  { key: 'forecast', title: '预测', icon: 'trend' },
  { key: 'actions', title: '建议动作', icon: 'check' },
] as const

const sections = computed(() =>
  SECTIONS.map((s) => ({
    key: s.key,
    title: s.title,
    icon: s.icon,
    text: data.value?.sections?.[s.key]?.text || '',
    chips: data.value?.sections?.[s.key]?.chips || [],
  })).filter((s) => s.text),
)

// 只看需要关注的几项，对比表默认折叠，避免一页塞太多
const compareOpen = ref(false)

// ---------------- 导入 ----------------
const sheetOpen = ref(false)
const step = ref<'mode' | 'form'>('mode')
const catalog = ref<{ key: string; name: string; items: CatalogField[] }[]>([])
const form = reactive({
  check_date: new Date().toISOString().slice(0, 10),
  source: 'manual' as 'manual' | 'photo' | 'pdf',
  hospital: '',
  note: '',
  file_path: '' as string,
  fileName: '',
})
const values = reactive<Record<string, string>>({})
const saving = ref(false)
const scanning = ref(false)
// 识别结果摘要：让用户知道「认出了几项 / 哪几项没填进去 / 哪里要核对」
const scanMeta = ref<{
  count: number
  warnings: string[]
  unmatched: { name: string; value: number; unit: string }[]
} | null>(null)
const photoInput = ref<HTMLInputElement | null>(null)
const pdfInput = ref<HTMLInputElement | null>(null)

async function openImport() {
  sheetOpen.value = true
  step.value = 'mode'
  scanMeta.value = null
  if (!catalog.value.length) {
    const r = await checkupApi.getCatalog()
    catalog.value = r.groups
  }
}

async function choose(mode: 'photo' | 'pdf' | 'manual') {
  if (!catalog.value.length) {
    const r = await checkupApi.getCatalog()
    catalog.value = r.groups
  }
  if (mode === 'photo') {
    photoInput.value?.click()
    return
  }
  if (mode === 'pdf') {
    pdfInput.value?.click()
    return
  }
  form.source = 'manual'
  form.file_path = ''
  form.fileName = ''
  scanMeta.value = null
  for (const k of Object.keys(values)) delete values[k]
  step.value = 'form'
}

/** 上传后直接识别：结果只做预填，用户核对原件后才保存 */
async function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.warning('文件超过 10 MB，请压缩后再传')
    return
  }
  scanning.value = true
  try {
    const r = await checkupApi.scanCheckup(file)
    if (!r.is_report) {
      ElMessage.warning('这张看起来不是体检报告，换一张再试')
      return
    }
    form.file_path = r.file_path
    form.fileName = r.filename
    form.source = r.source
    if (r.report_date) form.check_date = r.report_date
    if (r.hospital) form.hospital = r.hospital

    // 只覆盖识别出来的项，用户之前手填的其它项保留
    for (const it of r.items) values[it.key] = String(it.value)

    scanMeta.value = { count: r.metric_count, warnings: r.warnings, unmatched: r.unmatched }
    step.value = 'form'
    if (r.metric_count) {
      ElMessage.success(`识别出 ${r.metric_count} 项，请对照原件核对`)
    } else {
      ElMessage.warning('没识别出可用指标，可以手动填写')
    }
  } catch {
    // 错误提示已由 http 拦截器统一弹出，这里停在选方式页让用户重试
  } finally {
    scanning.value = false
  }
}

async function submit() {
  const metrics: Record<string, number> = {}
  for (const [k, v] of Object.entries(values)) {
    const n = Number(v)
    if (v !== '' && Number.isFinite(n)) metrics[k] = n
  }
  if (!Object.keys(metrics).length) {
    ElMessage.warning('至少填一项指标')
    return
  }
  saving.value = true
  try {
    await checkupApi.saveCheckup({
      check_date: form.check_date,
      source: form.source,
      hospital: form.hospital || undefined,
      note: form.note || undefined,
      file_path: form.file_path || undefined,
      metrics,
    })
    ElMessage.success('已保存，分析已更新')
    sheetOpen.value = false
    for (const k of Object.keys(values)) delete values[k]
    await load()
  } finally {
    saving.value = false
  }
}

// ---------------- 删除 ----------------
async function removeReport(id: number) {
  try {
    await ElMessageBox.confirm('删除后分析会按剩下的报告重算，确定删掉这份？', '删除体检报告', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  await checkupApi.deleteCheckup(id)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="an">
    <!-- 顶栏 -->
    <header class="an-top">
      <button class="ic-btn" aria-label="返回" @click="router.back()">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
      </button>
      <b>个人分析</b>
      <button class="ic-btn" aria-label="导入体检" @click="openImport">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      </button>
    </header>

    <p v-if="loading" class="loading">正在读取体检与打卡数据…</p>

    <template v-else-if="data">
      <!-- 没有报告 -->
      <section v-if="!data.has_report" class="card empty">
        <div class="empty-ico">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 3h8l4 4v14H4V3z" /><path d="M12 11v6M9 14h6" />
          </svg>
        </div>
        <h2>还没有体检报告</h2>
        <p>{{ data.headline }}</p>
        <p class="tip">
          没有报告也能用：分析会退回按身高体重推的估算值，但它不算你的基线，参考价值有限。
        </p>
      </section>

      <template v-else>
        <!-- 结论卡 -->
        <section class="hero">
          <div class="hero-k">体质判定 · 依据 {{ data.report ? dateText(data.report.check_date) : '最近一次' }} 体检</div>
          <div class="hero-type">{{ data.type }}</div>
          <p class="hero-line">{{ data.headline }}</p>
          <div v-if="data.chips.length" class="chips">
            <span v-for="c in data.chips" :key="c">{{ c }}</span>
          </div>
        </section>

        <!-- 指标卡 -->
        <section class="card">
          <div class="card-head">
            <h2>{{ data.report ? dateText(data.report.check_date) : '体检报告' }}</h2>
            <span class="head-tag">
              {{ data.metric_count }} 项 · {{ data.attention_count }} 项需关注
            </span>
          </div>
          <p class="src">
            {{ SOURCE_LABEL[data.report?.source || 'manual'] }}
            <template v-if="data.report?.hospital"> · {{ data.report.hospital }}</template>
            <a v-if="data.report?.file_path" :href="assetUrl(data.report.file_path)" target="_blank">查看原件</a>
          </p>

          <div v-for="g in data.groups" :key="g.key" class="grp">
            <div class="grp-t">{{ g.name }}</div>
            <div v-for="it in g.items" :key="it.key" class="m-row" :class="it.status">
              <div class="m-name">
                {{ it.name }}
                <i>{{ it.ref_text }}</i>
              </div>
              <div class="m-val">{{ it.value_text }}<em>{{ it.unit }}</em></div>
              <span class="m-tag">{{ statusText(it.status) }}</span>
            </div>
          </div>
        </section>

        <!-- 四段分析 -->
        <section v-for="s in sections" :key="s.key" class="card sec">
          <h2>{{ s.title }}</h2>
          <p>{{ s.text }}</p>
          <div v-if="s.chips.length" class="chips small">
            <span v-for="c in s.chips" :key="c">{{ c }}</span>
          </div>
        </section>

        <!-- 基线对比 -->
        <section class="card">
          <button class="card-head as-btn" :aria-expanded="compareOpen" @click="compareOpen = !compareOpen">
            <h2>指标对比</h2>
            <span class="head-tag">
              基线 {{ data.baseline_date }} → 现在
              <svg class="chev" :class="{ on: compareOpen }" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 9.5l6 6 6-6" />
              </svg>
            </span>
          </button>
          <div v-show="compareOpen" class="cmp">
            <div class="cmp-head">
              <span>指标</span><span>基线</span><span>现在</span><span>变化</span>
            </div>
            <div v-for="r in data.compare" :key="r.key" class="cmp-row">
              <span class="cmp-name">{{ r.name }}</span>
              <span>{{ r.baseline ?? '—' }}</span>
              <span class="cmp-now" :class="r.status">{{ r.now ?? '—' }}</span>
              <span
                class="cmp-delta"
                :class="{
                  down: (r.delta_raw ?? 0) < 0,
                  up: (r.delta_raw ?? 0) > 0,
                }"
              >
                {{ r.delta ? (r.delta_raw && r.delta_raw > 0 ? `+${r.delta}` : r.delta) : '—' }}
              </span>
            </div>
            <p class="cmp-foot">老师说的「开始前一次、中间一次」，价值就在这张表：数字变了，才证明方案有用。</p>
          </div>
        </section>

        <!-- 体检记录 -->
        <section v-if="data.reports.length" class="card">
          <h2>体检记录</h2>
          <div v-for="r in data.reports" :key="r.id" class="rep">
            <b>{{ r.check_date }}</b>
            <span>{{ r.metric_count }} 项 · {{ r.attention_count }} 项需关注</span>
            <em>{{ SOURCE_LABEL[r.source] }}</em>
            <button class="rep-del" aria-label="删除这份报告" @click="removeReport(r.id)">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 7h14M10 7V5h4v2M8 7l1 12h6l1-12" />
              </svg>
            </button>
          </div>
        </section>
      </template>
    </template>

    <button class="import-fab" @click="openImport">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      导入体检报告
    </button>

    <!-- 导入抽屉 -->
    <div class="mask" :class="{ on: sheetOpen }" @click="sheetOpen = false"></div>
    <div class="sheet" :class="{ on: sheetOpen }" role="dialog" aria-modal="true" aria-label="导入体检报告">
      <div class="sheet-grab"></div>

      <!-- 识别中：视觉模型单张 10~20 秒，PDF 两页更久 -->
      <div v-if="scanning" class="scanning">
        <i class="spin"></i>
        <b>正在识别报告…</b>
        <p>单张图片约 10~20 秒，PDF 两页可能更久，请先别退出</p>
      </div>

      <!-- 第一步：选方式 -->
      <template v-if="step === 'mode'">
        <h3>导入体检报告</h3>
        <p class="sheet-tip">拍照或上传 PDF 后自动识别指标，你核对原件后再保存。</p>
        <div class="modes">
          <button class="mode" :disabled="scanning" @click="choose('photo')">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 8h3l1.5-2h7L17 8h3v11H4z" /><circle cx="12" cy="13.5" r="3.2" />
            </svg>
            <b>拍照</b><em>纸质化验单</em>
          </button>
          <button class="mode" :disabled="scanning" @click="choose('pdf')">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /><path d="M10 12h5M10 16h5" />
            </svg>
            <b>上传 PDF</b><em>体检机构报告</em>
          </button>
          <button class="mode" :disabled="scanning" @click="choose('manual')">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 20h16" /><path d="M14.5 4.5l4 4L8 19H4v-4z" />
            </svg>
            <b>手动录入</b><em>只填关键项</em>
          </button>
        </div>
        <p class="sheet-foot">支持 jpg / png / webp / pdf，单个不超过 10 MB。</p>
      </template>

      <!-- 第二步：核对 / 填写 -->
      <template v-else>
        <h3>{{ form.source === 'manual' ? '手动录入体检指标' : '核对体检指标' }}</h3>
        <p v-if="form.fileName" class="sheet-tip file-tip">
          已保存：{{ form.fileName }}
          <a v-if="form.file_path" :href="assetUrl(form.file_path)" target="_blank">对照原件</a>
        </p>

        <div v-if="scanMeta" class="scan-note">
          <b>识别出 {{ scanMeta.count }} 项</b>
          <span>请对照原件逐项核对，改完再保存</span>
          <ul v-if="scanMeta.warnings.length">
            <li v-for="w in scanMeta.warnings" :key="w">{{ w }}</li>
          </ul>
          <p v-if="scanMeta.unmatched.length" class="unmatched">
            没填进去：
            <em v-for="u in scanMeta.unmatched" :key="u.name">
              {{ u.name }} {{ u.value }}{{ u.unit }}
            </em>
          </p>
        </div>

        <div class="sheet-body">
          <div class="row2">
            <label class="field">
              <span>体检日期</span>
              <input v-model="form.check_date" type="date" />
            </label>
            <label class="field">
              <span>体检机构（选填）</span>
              <input v-model="form.hospital" type="text" placeholder="例如：市第一医院" />
            </label>
          </div>

          <div v-for="g in catalog" :key="g.key" class="fgrp">
            <div class="fgrp-t">{{ g.name }}</div>
            <div class="fgrp-grid">
              <label v-for="it in g.items" :key="it.key" class="fitem">
                <span>{{ it.name }}<i v-if="it.unit">/ {{ it.unit }}</i></span>
                <input
                  v-model="values[it.key]"
                  type="number"
                  inputmode="decimal"
                  :placeholder="it.ref_text.replace('参考 ', '')"
                  step="any"
                />
              </label>
            </div>
          </div>
          <p class="form-tip">BMI 和基础代谢留空也行，会根据身高体重自动算。</p>
        </div>

        <div class="sheet-acts">
          <button class="btn" @click="step = 'mode'">上一步</button>
          <button class="btn btn-solid" :disabled="saving" @click="submit">
            {{ saving ? '保存中…' : '保存并分析' }}
          </button>
        </div>
      </template>
    </div>

    <input ref="photoInput" type="file" accept="image/*" capture="environment" hidden @change="onFile($event)" />
    <input ref="pdfInput" type="file" accept="application/pdf" hidden @change="onFile($event)" />
  </div>
</template>

<style scoped>
.an {
  --paper: #f0f7de;
  --teal: #c2e07c;
  --teal-dk: #b4d46a;
  --mint: #e9f3cc;
  --mint-2: #d3e79f;
  --ink: #171a14;
  --ink-2: #3f4639;
  --muted: #6d7565;
  --line: #e6edd6;
  --amber: #b8791a;
  --red: #be4a3c;
  --r-xl: 26px;
  --r-lg: 22px;
  --r-md: 16px;
  --shadow: 0 10px 28px rgba(28, 36, 22, 0.07);
  --shadow-lime: 0 18px 38px rgba(122, 152, 40, 0.2);

  margin: -10px 0 calc(-70px - env(safe-area-inset-bottom, 0px));
  padding: 0 0 calc(96px + env(safe-area-inset-bottom, 0px));
  min-height: calc(100vh - 52px);
  background: var(--paper);
  box-shadow: 0 0 0 10px var(--paper);
  color: var(--ink);
  font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'HarmonyOS Sans SC',
    'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.an * { box-sizing: border-box; }
.an h2, .an h3, .an p { margin: 0; }
.an svg { width: 100%; height: 100%; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

/* ---------- 顶栏 ---------- */
.an-top {
  position: sticky; top: 0; z-index: 6;
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px;
  background: rgba(240, 247, 222, 0.9);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid transparent;
}
.an-top b { font-size: 16px; font-weight: 800; }
.ic-btn {
  width: 34px; height: 34px; padding: 8px;
  border: none; border-radius: 12px;
  background: rgba(255, 255, 255, 0.7);
  color: var(--ink); cursor: pointer;
}
.ic-btn:active { background: var(--mint); }

/* ---------- 通用卡片 ---------- */
.card {
  margin: 0 14px 12px;
  padding: 16px;
  background: #fff;
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
}
.card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.card-head h2 { font-size: 15px; font-weight: 800; }
.head-tag { font-size: 11px; color: var(--muted); display: inline-flex; align-items: center; gap: 4px; }
.as-btn { width: 100%; padding: 0; border: none; background: none; text-align: left; cursor: pointer; }
.chev { width: 14px; height: 14px; transition: transform 0.18s; }
.chev.on { transform: rotate(180deg); }
.src { margin-top: 4px; font-size: 11.5px; color: var(--muted); }
.src a { margin-left: 6px; color: #5f7a1f; font-weight: 700; }

/* ---------- 结论卡 ---------- */
.hero {
  margin: 0 14px 12px;
  padding: 20px;
  background: var(--teal);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-lime);
}
.hero-k { font-size: 11.5px; color: var(--ink-2); }
.hero-type {
  margin-top: 4px;
  font-size: 26px; font-weight: 800; letter-spacing: -0.6px;
}
.hero-line { margin-top: 6px; font-size: 13px; color: var(--ink-2); line-height: 1.6; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.chips span {
  font-size: 11.5px; font-weight: 700;
  padding: 4px 10px; border-radius: 999px;
  background: rgba(255, 255, 255, 0.62); color: var(--ink);
}
.chips.small span { background: var(--mint); color: var(--ink-2); font-weight: 600; }

/* ---------- 指标卡 ---------- */
.grp { margin-top: 14px; }
.grp-t {
  font-size: 11.5px; font-weight: 800; color: var(--muted);
  letter-spacing: 0.4px; margin-bottom: 4px;
}
.m-row {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--line);
}
.m-row:last-child { border-bottom: none; }
.m-name { flex: 1; min-width: 0; font-size: 13px; font-weight: 700; }
.m-name i {
  display: block; font-style: normal;
  font-size: 10.5px; font-weight: 400; color: var(--muted);
}
.m-val { font-size: 14px; font-weight: 800; font-variant-numeric: tabular-nums; }
.m-val em { font-style: normal; font-size: 10.5px; font-weight: 500; color: var(--muted); margin-left: 2px; }
.m-tag {
  min-width: 40px; text-align: center;
  font-size: 10.5px; font-weight: 800;
  padding: 3px 8px; border-radius: 999px;
  background: var(--mint); color: var(--ink-2);
}
.m-row.high .m-val { color: var(--amber); }
.m-row.high .m-tag { background: #fbf0d8; color: var(--amber); }
.m-row.low .m-val { color: var(--red); }
.m-row.low .m-tag { background: #f8e4e1; color: var(--red); }

/* ---------- 四段 ---------- */
.sec h2 {
  font-size: 13px; font-weight: 800; color: var(--ink-2);
  letter-spacing: 0.2px; margin-bottom: 6px;
}
.sec p { font-size: 13px; line-height: 1.72; color: var(--ink-2); }

/* ---------- 对比表 ---------- */
.cmp { margin-top: 10px; }
.cmp-head, .cmp-row {
  display: grid; grid-template-columns: 1.3fr 0.8fr 0.8fr 0.8fr;
  gap: 6px; align-items: center;
  font-size: 12px;
  padding: 7px 0;
}
.cmp-head { color: var(--muted); font-size: 11px; border-bottom: 1px solid var(--line); }
.cmp-row { border-bottom: 1px dashed var(--line); font-variant-numeric: tabular-nums; }
.cmp-row:last-child { border-bottom: none; }
.cmp-name { font-weight: 700; }
.cmp-now { font-weight: 800; }
.cmp-now.high { color: var(--amber); }
.cmp-now.low { color: var(--red); }
.cmp-delta { color: var(--muted); }
.cmp-delta.down { color: #4a7a1c; }
.cmp-delta.up { color: var(--amber); }
.cmp-foot { margin-top: 10px; font-size: 11.5px; color: var(--muted); line-height: 1.6; }

/* ---------- 体检记录 ---------- */
.rep {
  display: flex; align-items: baseline; gap: 8px;
  padding: 9px 0; border-bottom: 1px dashed var(--line);
  font-size: 12.5px;
}
.rep:last-child { border-bottom: none; }
.rep b { font-weight: 800; }
.rep span { color: var(--muted); }
.rep em { margin-left: auto; font-style: normal; font-size: 11px; color: var(--muted); }
.rep-del {
  width: 26px; height: 26px; padding: 5px;
  border: none; border-radius: 8px;
  background: transparent; color: var(--muted);
  cursor: pointer;
}
.rep-del:active { background: var(--mint); color: var(--red); }

/* ---------- 空态 ---------- */
.empty { text-align: center; padding: 28px 18px 22px; }
.empty-ico {
  width: 44px; height: 44px; margin: 0 auto 10px; padding: 10px;
  border-radius: 14px; background: var(--mint); color: var(--ink-2);
}
.empty h2 { font-size: 16px; font-weight: 800; }
.empty p { margin-top: 6px; font-size: 12.5px; color: var(--muted); line-height: 1.7; }
.empty .tip { margin-top: 12px; font-size: 11px; color: #8b937f; }

/* ---------- 按钮 ---------- */
.btn {
  flex: 1; height: 44px; padding: 0 14px;
  border: none; border-radius: 14px;
  background: #fff; color: var(--ink);
  font-size: 13.5px; font-weight: 700;
  box-shadow: inset 0 0 0 1px var(--line);
  cursor: pointer;
}
.btn:active { transform: scale(0.98); }
.btn-solid { background: var(--teal); box-shadow: 0 10px 22px rgba(122, 152, 40, 0.22); }
.loading { padding: 40px 0; text-align: center; font-size: 13px; color: var(--muted); }

.import-fab {
  position: fixed; left: 16px; right: 16px;
  bottom: calc(72px + env(safe-area-inset-bottom, 0px));
  height: 48px;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  border: none; border-radius: 16px;
  background: var(--teal); color: var(--ink);
  font-size: 14px; font-weight: 800;
  box-shadow: var(--shadow-lime);
  cursor: pointer; z-index: 7;
}
.import-fab svg { width: 16px; height: 16px; }

/* ---------- 抽屉 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 30;
  background: rgba(20, 26, 16, 0.32);
  opacity: 0; pointer-events: none; transition: opacity 0.2s;
}
.mask.on { opacity: 1; pointer-events: auto; }
.sheet {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 31;
  max-height: 88vh; overflow-y: auto;
  padding: 10px 16px calc(20px + env(safe-area-inset-bottom, 0px));
  background: var(--paper);
  border-radius: 26px 26px 0 0;
  transform: translateY(102%);
  transition: transform 0.26s cubic-bezier(0.32, 0.72, 0, 1);
  box-shadow: 0 -12px 40px rgba(28, 36, 22, 0.16);
}
.sheet.on { transform: translateY(0); }
.sheet-grab {
  width: 38px; height: 4px; margin: 2px auto 12px;
  border-radius: 999px; background: #cfd8bd;
}

/* ---------- 识别中 ---------- */
.scanning {
  position: absolute; inset: 0; z-index: 5;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 34px;
  background: rgba(240, 247, 222, 0.97);
  border-radius: 26px 26px 0 0;
  text-align: center;
}
.scanning b { font-size: 15px; font-weight: 800; }
.scanning p { font-size: 11.5px; color: var(--muted); line-height: 1.65; }
.spin {
  width: 30px; height: 30px; border-radius: 50%;
  border: 3px solid var(--mint-2);
  border-top-color: #5f7a1f;
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ---------- 识别结果提示 ---------- */
.scan-note {
  margin-top: 10px; padding: 10px 12px;
  border-radius: 14px; background: var(--mint);
  font-size: 11.5px; color: var(--ink-2); line-height: 1.65;
}
.scan-note b { font-size: 12.5px; font-weight: 800; margin-right: 6px; }
.scan-note ul { margin: 6px 0 0; padding-left: 16px; color: #8a5a12; }
.scan-note li { margin-top: 2px; }
.scan-note .unmatched { margin: 6px 0 0; color: var(--muted); }
.scan-note .unmatched em { display: inline-block; margin-right: 8px; font-style: normal; }
.file-tip a { margin-left: 6px; color: #5f7a1f; font-weight: 700; }
.sheet h3 { font-size: 16px; font-weight: 800; }
.sheet-tip { margin-top: 4px; font-size: 12px; color: var(--muted); line-height: 1.6; }
.file-tip { color: #5f7a1f; }
.sheet-body { margin-top: 12px; }

.modes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 14px; }
.mode {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  padding: 16px 8px;
  border: none; border-radius: var(--r-md);
  background: #fff; color: var(--ink);
  box-shadow: var(--shadow);
  cursor: pointer;
}
.mode:active { background: var(--mint); }
.mode svg { width: 22px; height: 22px; color: var(--ink-2); }
.mode b { font-size: 12.5px; font-weight: 800; }
.mode em { font-style: normal; font-size: 10px; color: var(--muted); }
.sheet-foot { margin-top: 12px; font-size: 11px; color: #8b937f; text-align: center; }

.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field span { font-size: 11.5px; color: var(--muted); }
.field input, .fitem input {
  height: 40px; padding: 0 10px;
  border: none; border-radius: 12px;
  background: #fff; color: var(--ink);
  font-size: 13px;
  box-shadow: inset 0 0 0 1px var(--line);
  outline: none;
}
.field input:focus, .fitem input:focus { box-shadow: inset 0 0 0 1.5px var(--mint-2); }

.fgrp { margin-top: 14px; }
.fgrp-t {
  font-size: 11.5px; font-weight: 800; color: var(--muted);
  letter-spacing: 0.4px; margin-bottom: 6px;
}
.fgrp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.fitem { display: flex; flex-direction: column; gap: 4px; }
.fitem span { font-size: 11.5px; color: var(--ink-2); }
.fitem span i { font-style: normal; color: var(--muted); font-size: 10.5px; }
.form-tip { margin-top: 12px; font-size: 11px; color: #8b937f; }

.sheet-acts { display: flex; gap: 8px; margin-top: 16px; }
</style>
