<script setup lang="ts">
// 健康档案页：基础信息 + 三组偏好 + BMI 实时展示 + 体重记录曲线
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as profileApi from '@/api/profile'
import TrendChart from '@/components/TrendChart.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const weights = ref<{ weight_kg: number; recorded_date: string }[]>([])

const form = reactive({
  height_cm: null as number | null,
  weight_kg: null as number | null,
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
  Object.assign(form, {
    height_cm: p.height_cm, weight_kg: p.weight_kg, age: p.age,
    gender: p.gender ?? 'male', activity_level: p.activity_level, goal: p.goal,
    target_weight_kg: p.target_weight_kg,
    taste_preferences: p.taste_preferences, dietary_restrictions: p.dietary_restrictions,
    exercise_preferences: p.exercise_preferences,
  })
  weights.value = await profileApi.getWeights()
}

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
      <el-col :span="16">
        <div class="page-card">
          <h3 class="page-title">📋 基础信息</h3>
          <el-form label-width="90px">
            <el-row :gutter="12">
              <el-col :span="8"><el-form-item label="身高(cm)"><el-input-number v-model="form.height_cm" :min="50" :max="250" style="width:100%" /></el-form-item></el-col>
              <el-col :span="8"><el-form-item label="体重(kg)"><el-input-number v-model="form.weight_kg" :min="20" :max="300" style="width:100%" /></el-form-item></el-col>
              <el-col :span="8"><el-form-item label="年龄"><el-input-number v-model="form.age" :min="5" :max="110" style="width:100%" /></el-form-item></el-col>
            </el-row>
            <el-row :gutter="12">
              <el-col :span="8">
                <el-form-item label="性别">
                  <el-radio-group v-model="form.gender">
                    <el-radio value="male">男</el-radio>
                    <el-radio value="female">女</el-radio>
                  </el-radio-group>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="活动水平">
                  <el-select v-model="form.activity_level">
                    <el-option label="久坐少动" value="sedentary" />
                    <el-option label="轻度活动" value="light" />
                    <el-option label="中度活动" value="moderate" />
                    <el-option label="高度活动" value="active" />
                    <el-option label="运动员级" value="athlete" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="目标">
                  <el-radio-group v-model="form.goal">
                    <el-radio value="lose">减脂</el-radio>
                    <el-radio value="maintain">维持</el-radio>
                    <el-radio value="gain">增肌</el-radio>
                  </el-radio-group>
                </el-form-item>
              </el-col>
            </el-row>
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

          <el-button type="success" :loading="loading" @click="save">保存档案</el-button>
        </div>
      </el-col>

      <el-col :span="8">
        <div class="page-card" style="text-align:center">
          <h3 class="page-title" style="justify-content:center">BMI 指数</h3>
          <div class="bmi" :style="{ color: bmiColor }">{{ bmiPreview ?? '--' }}</div>
          <el-tag :color="bmiColor" style="color:#fff;border:none">
            {{ bmiPreview == null ? '请先填写身高体重' : bmiPreview < 18.5 ? '偏瘦' : bmiPreview < 24 ? '正常' : bmiPreview < 28 ? '超重' : '肥胖' }}
          </el-tag>
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
  </div>
</template>

<style scoped>
.bmi { font-size: 42px; font-weight: 800; margin: 4px 0 8px; }
.page-title { margin-top: 18px; }
</style>
