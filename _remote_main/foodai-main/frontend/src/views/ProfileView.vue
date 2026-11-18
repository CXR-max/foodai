<script setup lang="ts">
// 健康档案页：基础信息 + 诉求 + 三组偏好 + BMI + 处方卡（为什么给你这个数）
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as profileApi from '@/api/profile'
import BasisPopover from '@/components/BasisPopover.vue'
import TrendChart from '@/components/TrendChart.vue'
import { useAuthStore } from '@/stores/auth'
import { isMobile } from '@/composables/useIsMobile'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)
const weights = ref<{ weight_kg: number; recorded_date: string }[]>([])
const profile = ref<profileApi.Profile | null>(null)

// 诉求四选一：诉求决定策略，BMI/体脂只决定强度（不按 BMI 一刀切）
const GOALS = [
  { value: 'lose', label: '减脂', desc: '按 BMI 给 10~20% 赤字，慢慢减' },
  { value: 'maintain', label: '维持', desc: '吃够维持量，体重基本不变' },
  { value: 'gain', label: '增肌', desc: '小幅盈余 + 力量训练' },
  { value: 'healthy', label: '健康饮食', desc: '不设赤字，重点盐糖蔬菜' },
]

// 手机端底部只有 5 个 tab，其余功能从这里进（桌面端由左侧菜单承担，不显示这块）
const moreEntries = [
  { path: '/stats', title: '数据追踪', icon: '📈', desc: '热量趋势、营养结构、体重变化' },
  { path: '/mall', title: '积分商城', icon: '🪙', desc: '用积分兑换奖励' },
  { path: '/achievements', title: '成就徽章', icon: '🏆', desc: '查看已解锁的成就' },
  { path: '/leaderboard', title: '排行榜', icon: '🥇', desc: '和好友比一比积分' },
]

const form = reactive({
  height_cm: null as number | null,
  weight_kg: null as number | null,
  body_fat_pct: null as number | null,
  age: null as number | null,
  gender: 'male',
  activity_level: 'moderate',
  goal: 'maintain',
  target_weight_kg: null as number | null,
  taste_preferences: [] as string[],
  dietary_restrictions: [] as string[],
  exercise_preferences: [] as string[],
})

// 偏好可选项（接真模型后模型可理解任意选项；此处给大众化常用清单）
const TASTES = ['清淡', '咸鲜', '香辣', '微辣', '麻辣', '酸辣', '偏甜', '偏酸', '偏咸', '蒜香', '孜然', '酱香', '烧烤', '鲜香']
const RESTRICTIONS = ['素食', '蛋奶素', '忌辣', '少油', '低糖', '低盐', '海鲜过敏', '花生过敏', '坚果过敏', '乳糖不耐', '麸质过敏', '大豆过敏', '不吃猪肉', '清真', '不吃牛肉']
const EXERCISES = ['跑步', '快走', '散步', '骑行', '游泳', '瑜伽', '普拉提', '力量训练', '健身操', '跳绳', '羽毛球', '篮球', '足球', '乒乓球', '网球', '登山', '徒步', '舞蹈', '广场舞', '太极拳', '八段锦', 'HIIT']

// BMI 前端实时预览（保存后以后端计算为准）
const bmiPreview = computed(() => {
  if (!form.height_cm || !form.weight_kg) return null
  return +(form.weight_kg / (form.height_cm / 100) ** 2).toFixed(1)
})
const bmiColor = computed(() => {
  const b = bmiPreview.value
  if (b == null) return '#909399'
  if (b < 18.5) return '#409eff'
  if (b < 24) return '#67c23a'
  if (b < 28) return '#e6a23c'
  return '#f56c6c'
})

const newWeight = ref<number | null>(null)

async function load() {
  const p = await profileApi.getProfile()
  profile.value = p
  Object.assign(form, {
    height_cm: p.height_cm, weight_kg: p.weight_kg, body_fat_pct: p.body_fat_pct, age: p.age,
    gender: p.gender ?? 'male', activity_level: p.activity_level, goal: p.goal,
    target_weight_kg: p.target_weight_kg,
    taste_preferences: p.taste_preferences, dietary_restrictions: p.dietary_restrictions,
    exercise_preferences: p.exercise_preferences,
  })
  weights.value = await profileApi.getWeights()
}

/** 处方与目标依据 */
const rx = computed(() => profile.value?.prescription ?? null)
const tg = computed(() => profile.value?.targets ?? null)
const src = (key: string) => tg.value?._source?.[key] ?? null

/** 处方卡上要展示的五行目标（每行都能点开看依据） */
const TARGET_ROWS = [
  { key: 'calorie_min', label: '热量', unit: 'kcal', pair: 'calorie_max' },
  { key: 'protein_min_g', label: '蛋白', unit: 'g', pair: 'protein_max_g' },
  { key: 'fat_min_g', label: '脂肪', unit: 'g', pair: 'fat_max_g' },
  { key: 'carb_min_g', label: '碳水', unit: 'g', pair: 'carb_max_g' },
  { key: 'water_min_ml', label: '饮水', unit: 'ml', pair: 'water_max_ml' },
  { key: 'exercise_burn_min', label: '运动消耗', unit: 'kcal', pair: 'exercise_burn_max' },
]

const targetRows = computed(() =>
  TARGET_ROWS.map((row) => {
    const t = tg.value as Record<string, unknown> | null
    const lo = t?.[row.key] as number | undefined
    const hi = t?.[row.pair] as number | undefined
    if (lo == null && hi == null) return null
    return { ...row, text: `${lo ?? '—'}~${hi ?? '—'} ${row.unit}` }
  }).filter(Boolean) as { key: string; label: string; unit: string; pair: string; text: string }[],
)

async function save() {
  loading.value = true
  try {
    const p = await profileApi.saveProfile({ ...form })
    ElMessage.success('档案已保存')
    if (p.profile_completed) ElMessage.success('档案完善奖励已到账 🎉')
    auth.fetchMe().catch(() => {})   // 顶栏积分实时刷新
    await load()
  } finally {
    loading.value = false
  }
}

async function addWeight() {
  if (!newWeight.value) return
  const r = await profileApi.addWeight({ weight_kg: newWeight.value })
  ElMessage.success(r.points_awarded ? `体重已记录 +${r.points_awarded} 分` : '体重已记录')
  newWeight.value = null
  await load()
}

// 体重曲线配置
const weightOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 20, top: 20, bottom: 30 },
  xAxis: { type: 'category', data: weights.value.map((w) => w.recorded_date.slice(5)) },
  yAxis: { type: 'value', scale: true, name: 'kg' },
  series: [{
    type: 'line', smooth: true, data: weights.value.map((w) => w.weight_kg),
    lineStyle: { color: '#67c23a' }, itemStyle: { color: '#67c23a' },
    areaStyle: { color: 'rgba(103,194,58,0.12)' },
  }],
}))

onMounted(load)
</script>

<template>
  <div class="page">
    <el-row :gutter="16">
      <el-col :span="16" :xs="24">
        <div class="page-card">
          <h3 class="page-title">📋 基础信息</h3>
          <el-form label-width="90px" :label-position="isMobile ? 'top' : 'right'">
            <el-row :gutter="12">
              <el-col :span="8" :xs="24"><el-form-item label="身高(cm)"><el-input-number v-model="form.height_cm" :min="50" :max="250" style="width:100%" /></el-form-item></el-col>
              <el-col :span="8" :xs="12"><el-form-item label="体重(kg)"><el-input-number v-model="form.weight_kg" :min="20" :max="300" style="width:100%" /></el-form-item></el-col>
              <el-col :span="8" :xs="12"><el-form-item label="年龄"><el-input-number v-model="form.age" :min="5" :max="110" style="width:100%" /></el-form-item></el-col>
            </el-row>
            <el-row :gutter="12">
              <el-col :span="8" :xs="24">
                <el-form-item label="体脂率(%)">
                  <el-input-number v-model="form.body_fat_pct" :min="3" :max="70" :precision="1" style="width:100%" placeholder="选填" />
                </el-form-item>
              </el-col>
              <el-col :span="16" :xs="24">
                <p class="bf-tip">选填 · 肌肉占比越高同运动消耗越多，不填按 BMI 估算</p>
              </el-col>
            </el-row>
            <el-row :gutter="12">
              <el-col :span="8" :xs="24">
                <el-form-item label="性别">
                  <el-radio-group v-model="form.gender">
                    <el-radio value="male">男</el-radio>
                    <el-radio value="female">女</el-radio>
                  </el-radio-group>
                </el-form-item>
              </el-col>
              <el-col :span="12" :xs="24">
                <el-form-item label="活动水平">
                  <el-select v-model="form.activity_level" style="width:100%">
                    <el-option label="久坐少动" value="sedentary" />
                    <el-option label="轻度活动" value="light" />
                    <el-option label="中度活动" value="moderate" />
                    <el-option label="高度活动" value="active" />
                    <el-option label="运动员级" value="athlete" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="目标诉求">
              <div class="goal-grid">
                <div
                  v-for="g in GOALS"
                  :key="g.value"
                  class="goal-item"
                  :class="{ active: form.goal === g.value }"
                  @click="form.goal = g.value"
                >
                  <span class="goal-name">{{ g.label }}</span>
                  <span class="goal-desc">{{ g.desc }}</span>
                </div>
              </div>
            </el-form-item>
            <el-form-item label="目标体重"><el-input-number v-model="form.target_weight_kg" :min="20" :max="300" /></el-form-item>
          </el-form>

          <h3 class="page-title">😋 口味偏好（AI 会优先满足）</h3>
          <el-checkbox-group v-model="form.taste_preferences">
            <el-checkbox v-for="t in TASTES" :key="t" :value="t">{{ t }}</el-checkbox>
          </el-checkbox-group>

          <h3 class="page-title">🚫 饮食禁忌（AI 绝对排除）</h3>
          <el-checkbox-group v-model="form.dietary_restrictions">
            <el-checkbox v-for="t in RESTRICTIONS" :key="t" :value="t">{{ t }}</el-checkbox>
          </el-checkbox-group>

          <h3 class="page-title">🏃 运动兴趣（AI 优先安排）</h3>
          <el-checkbox-group v-model="form.exercise_preferences">
            <el-checkbox v-for="t in EXERCISES" :key="t" :value="t">{{ t }}</el-checkbox>
          </el-checkbox-group>

          <el-button type="success" :loading="loading" :style="isMobile ? 'width:100%' : ''" @click="save">保存档案</el-button>
        </div>
      </el-col>

      <el-col :span="8" :xs="24">
        <div class="page-card" style="text-align:center">
          <h3 class="page-title" style="justify-content:center">BMI 指数</h3>
          <div class="bmi" :style="{ color: bmiColor }">{{ bmiPreview ?? '--' }}</div>
          <el-tag :color="bmiColor" style="color:#fff;border:none">
            {{ bmiPreview == null ? '请先填写身高体重' : bmiPreview < 18.5 ? '偏瘦' : bmiPreview < 24 ? '正常' : bmiPreview < 28 ? '超重' : '肥胖' }}
          </el-tag>
        </div>

        <div v-if="rx" class="page-card rx-card">
          <h3 class="page-title">🧭 为什么给你这个数</h3>
          <p class="rx-summary">{{ rx.summary }}</p>
          <div class="rx-grid">
            <div class="rx-cell"><span class="rx-k">维持热量</span><b class="rx-v">{{ rx.tdee ?? '—' }}</b></div>
            <div class="rx-cell"><span class="rx-k">每日目标</span><b class="rx-v">{{ rx.target }}</b></div>
            <div class="rx-cell"><span class="rx-k">预期速度</span><b class="rx-v">{{ rx.rate_text }}</b></div>
            <div class="rx-cell"><span class="rx-k">每周训练</span><b class="rx-v">{{ rx.train_days }} 天</b></div>
          </div>
          <div class="rx-targets">
            <div v-for="row in targetRows" :key="row.key" class="rx-line">
              <span class="rx-line-k">{{ row.label }}</span>
              <span class="rx-line-v">{{ row.text }}</span>
              <BasisPopover :source="src(row.key)" :name="row.label" />
            </div>
          </div>
          <div class="rx-focus">
            <span v-for="f in rx.focus" :key="f" class="rx-chip">{{ f }}</span>
          </div>
          <p v-for="w in rx.warnings" :key="w" class="rx-warn">⚠️ {{ w }}</p>
          <p class="rx-basis">{{ rx.basis_weight_note }}</p>
          <p class="rx-foot">健康管理建议，非医疗器械。糖尿病、肾病、痛风、孕期请遵医嘱。</p>
        </div>
        <div v-else class="page-card">
          <h3 class="page-title">🧭 为什么给你这个数</h3>
          <p class="rx-summary">填完身高、体重、年龄，这里会给出热量策略、预期速度和每个数字的依据。</p>
        </div>

        <div class="page-card">
          <h3 class="page-title">⚖️ 记录今日体重</h3>
          <div style="display:flex;gap:8px">
            <el-input-number v-model="newWeight" :min="20" :max="300" :precision="1" style="flex:1" placeholder="kg" />
            <el-button type="success" @click="addWeight">记录</el-button>
          </div>
          <template v-if="weights.length">
            <TrendChart :option="weightOption" height="200px" style="margin-top:12px" />
          </template>
          <el-empty v-else description="还没有体重记录" :image-size="60" />
        </div>
      </el-col>
    </el-row>

    <!-- 手机端：底部 tab 之外的入口聚合在这里（桌面端左侧菜单已有，不重复显示） -->
    <div v-if="isMobile" class="page-card more-card">
      <h3 class="page-title">更多功能</h3>
      <div
        v-for="e in moreEntries"
        :key="e.path"
        class="more-item"
        @click="router.push(e.path)"
      >
        <span class="more-icon">{{ e.icon }}</span>
        <span class="more-body">
          <span class="more-title">{{ e.title }}</span>
          <span class="more-desc">{{ e.desc }}</span>
        </span>
        <span class="more-arrow">›</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bmi { font-size: 42px; font-weight: 800; margin: 4px 0 8px; }
.page-title { margin-top: 18px; }
.bf-tip { margin: 0; font-size: 12px; color: #909399; line-height: 1.5; padding-top: 8px; }

/* 诉求四选一：诉求决定策略，不是按 BMI 一刀切 */
.goal-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
}
.goal-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border: 1px solid #e8eaf0;
  border-radius: 10px;
  background: #fbfcfe;
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.goal-item:hover { border-color: #c6d4ea; background: #f5f9ff; }
.goal-item.active { border-color: #67c23a; background: #f2fbef; box-shadow: 0 0 0 1px #67c23a inset; }
.goal-name { font-size: 13px; font-weight: 600; color: #303133; }
.goal-desc { font-size: 11px; color: #909399; line-height: 1.45; }

/* 处方卡 */
.rx-summary { margin: 0 0 10px; font-size: 12.5px; line-height: 1.6; color: #4b5563; }
.rx-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 10px; }
.rx-cell {
  display: flex; flex-direction: column; gap: 1px;
  padding: 8px 10px; border-radius: 8px; background: #f7f9fc;
}
.rx-k { font-size: 11px; color: #8a94a6; }
.rx-v { font-size: 14px; color: #303133; }
.rx-targets { border-top: 1px dashed #eceff4; padding-top: 8px; margin-bottom: 10px; }
.rx-line {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; padding: 3px 0; color: #606266;
}
.rx-line-k { width: 56px; color: #8a94a6; flex: none; }
.rx-line-v { flex: 1; color: #303133; font-variant-numeric: tabular-nums; }
.rx-focus { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.rx-chip {
  padding: 2px 8px; border-radius: 999px; background: #eef6ff;
  color: #2b6cd4; font-size: 11px;
}
.rx-warn { margin: 4px 0 0; font-size: 11.5px; line-height: 1.55; color: #b07a1e; }
.rx-basis { margin: 8px 0 0; font-size: 11px; color: #909399; line-height: 1.5; }
.rx-foot { margin: 6px 0 0; font-size: 10.5px; color: #b6bcc8; line-height: 1.5; }

/* 手机端「更多功能」入口列表 */
.more-card { padding-bottom: 6px; }
.more-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 2px;
  border-bottom: 1px solid #f2f3f5;
  cursor: pointer;
}
.more-item:last-child { border-bottom: none; }
.more-item:active { background: #f7f8fa; }
.more-icon { font-size: 20px; width: 26px; text-align: center; }
.more-body { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.more-title { font-size: 14px; color: #303133; font-weight: 500; }
.more-desc { font-size: 11px; color: #a8abb2; }
.more-arrow { color: #c0c4cc; font-size: 18px; }
</style>
