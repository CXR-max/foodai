<script setup lang="ts">
// 「依据」小问号：点开显示这个数字怎么来的
// 三种性质严格分开措辞——guideline 才许写「依据《XX》」，heuristic 只能说「按常规取值」
import { computed } from 'vue'
import type { TargetSource } from '@/api/profile'

const props = defineProps<{
  source?: TargetSource | null
  /** 指标中文名，弹窗标题用 */
  name?: string
}>()

const KIND = {
  guideline: { text: '指南原文', tone: 'ok' },
  derived: { text: '公式推算', tone: 'mid' },
  heuristic: { text: '经验取值', tone: 'soft' },
} as const

const kind = computed(() => {
  const k = (props.source?.kind || 'heuristic') as keyof typeof KIND
  return KIND[k] || KIND.heuristic
})

const hasData = computed(() => Boolean(props.source?.label || props.source?.basis))
</script>

<template>
  <el-popover v-if="hasData" placement="top" :width="286" trigger="click" popper-class="basis-pop">
    <template #reference>
      <span class="basis-dot" @click.stop>?</span>
    </template>
    <div class="basis-body">
      <div class="basis-head">
        <span class="basis-kind" :class="kind.tone">{{ kind.text }}</span>
        <span v-if="source?.id" class="basis-id">{{ source.id }}</span>
      </div>
      <p v-if="name" class="basis-name">{{ name }}</p>
      <p v-if="source?.label" class="basis-label">{{ source.label }}</p>
      <p v-if="source?.basis" class="basis-note">{{ source.basis }}</p>
      <p v-if="kind.tone === 'soft'" class="basis-warn">这类数值是本系统的工程取值，没有指南直接给出这个数。</p>
    </div>
  </el-popover>
</template>

<style scoped>
.basis-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  margin-left: 4px;
  border-radius: 50%;
  background: #eef1f6;
  color: #8a94a6;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  vertical-align: middle;
  user-select: none;
}
.basis-dot:hover { background: #e3e8f0; color: #5b6b83; }
.basis-body { font-size: 12px; line-height: 1.6; }
.basis-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.basis-kind { padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.basis-kind.ok { background: #e8f6ee; color: #1f9254; }
.basis-kind.mid { background: #eef4ff; color: #2b6cd4; }
.basis-kind.soft { background: #f4f4f6; color: #7a7f8a; }
.basis-id { color: #b6bcc8; font-size: 11px; }
.basis-name { margin: 0 0 2px; font-weight: 600; color: #303133; }
.basis-label { margin: 0 0 4px; color: #4b5563; }
.basis-note { margin: 0; color: #8a94a6; }
.basis-warn { margin: 6px 0 0; color: #b07a1e; font-size: 11px; }
</style>
