<script setup lang="ts">
// ECharts 通用图表组件：传 option 即渲染，自动自适应宽度
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

// option 用宽松类型：图表配置项非常多，严格类型反而难写（修改时参考 echarts 官网文档即可）
const props = defineProps<{ option: Record<string, any>; height?: string }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

onMounted(() => {
  chart = echarts.init(el.value!)
  chart.setOption(props.option)
  window.addEventListener('resize', resize)
})

watch(() => props.option, (opt) => chart?.setOption(opt, true), { deep: true })

function resize() { chart?.resize() }

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
})
</script>

<template>
  <div ref="el" :style="{ width: '100%', height: height || '320px' }" />
</template>
