<script setup lang="ts">
// 人脸采集弹窗（移动端优先）
// 职责：拿到"活人"的现场照（多帧），交给父组件比对/绑定
//
// 活体方案（B 档）：
// - MediaPipe FaceLandmarker 输出 blendshapes，读 eyeBlink / jawOpen 做真实动作检测
//   （静态照片没有"睁→闭→睁"的时序，过不了）
// - 每次随机要求「眨眼」或「张嘴」，预录视频对不上随机指令
// - 通过后连拍 3 帧上传，服务端再校验帧间差异 + 1:1 比对
// - MediaPipe 加载失败时降级为画面运动检测（兼容模式，界面会标注）
import { computed, nextTick, ref, watch } from 'vue'
import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision'

const props = defineProps<{
  visible: boolean
  title?: string
  tip?: string
  allowUpload?: boolean
}>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'captured', images: string[]): void
}>()

type Action = 'blink' | 'mouth'

const visible = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
})

const video = ref<HTMLVideoElement>()
const stage = ref<'loading' | 'action' | 'passed' | 'error' | 'unsupported'>('loading')
const hint = ref('正在准备…')
const errorMsg = ref('')
const guide = ref('')
const fileInput = ref<HTMLInputElement>()
const action = ref<Action>('blink')
const compat = ref(false) // true = MediaPipe 不可用，降级运动检测

let stream: MediaStream | null = null
let raf = 0
let timer: number | undefined
let prev: Uint8ClampedArray | null = null
let motions = 0
let lastMotionAt = 0
let phase: 'waitRest' | 'waitAction' = 'waitRest'
let lastTs = 0

// ---------------- MediaPipe ----------------
let landmarker: FaceLandmarker | null = null
let lmPromise: Promise<boolean> | null = null

async function loadLandmarker(): Promise<boolean> {
  if (landmarker) return true
  if (!lmPromise) {
    lmPromise = (async () => {
      try {
        const fileset = await FilesetResolver.forVisionTasks('/mediapipe/wasm')
        try {
          landmarker = await FaceLandmarker.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: '/models/face_landmarker.task', delegate: 'GPU' },
            runningMode: 'VIDEO',
            numFaces: 1,
            outputFaceBlendshapes: true,
          })
        } catch {
          // 部分手机 GPU 委托不可用，退回 CPU
          landmarker = await FaceLandmarker.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: '/models/face_landmarker.task', delegate: 'CPU' },
            runningMode: 'VIDEO',
            numFaces: 1,
            outputFaceBlendshapes: true,
          })
        }
        return true
      } catch {
        return false
      }
    })()
  }
  return lmPromise
}

// ---------------- 摄像头 ----------------
const probe = document.createElement('canvas')
probe.width = 96
probe.height = 72
const probeCtx = probe.getContext('2d', { willReadFrequently: true })!

function stopCamera() {
  cancelAnimationFrame(raf)
  if (timer) window.clearTimeout(timer)
  timer = undefined
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
  prev = null
}

function fail(msg: string, howTo = '') {
  stage.value = 'unsupported'
  hint.value = '无法使用摄像头'
  errorMsg.value = msg
  guide.value = howTo
  stopCamera()
}

const BLOCKED: Array<[string, string]> = [
  ['micromessenger', '微信'], ['quark', '夸克'], ['ucbrowser', 'UC 浏览器'],
  ['mqqbrowser', 'QQ 浏览器'], ['qqbrowser', 'QQ 浏览器'], ['baiduboxapp', '百度 App'],
  ['feishu', '飞书'], ['lark', '飞书'], ['dingtalk', '钉钉'], ['weibo', '微博'],
]

async function start() {
  stage.value = 'loading'
  hint.value = '正在打开摄像头…'
  errorMsg.value = ''
  guide.value = ''
  motions = 0
  lastMotionAt = 0
  phase = 'waitRest'

  const ua = navigator.userAgent.toLowerCase()
  const hit = BLOCKED.find(([k]) => ua.includes(k))
  if (hit) {
    fail(`${hit[1]}的内置浏览器不支持调用摄像头`, '请复制本页地址，用 Safari（iPhone）或 Chrome（安卓）打开')
    return
  }
  if (!window.isSecureContext) {
    fail('当前页面不是 HTTPS，浏览器禁止调用摄像头', '请用 https 开头的地址访问')
    return
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    fail('当前浏览器不支持调用摄像头', '建议换 Chrome、Safari、Edge')
    return
  }

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 720 }, height: { ideal: 960 } },
      audio: false,
    })
  } catch (e: any) {
    const name = e?.name || ''
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      fail('摄像头权限被拒绝', '点地址栏左侧的锁/相机图标 → 允许 → 刷新页面')
    } else if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      fail('没有检测到前置摄像头', '换一台设备，或检查系统相机是否被禁用')
    } else if (name === 'NotReadableError') {
      fail('摄像头被其他应用占用', '关掉视频通话/相机 App 后重试')
    } else {
      fail(`摄像头打开失败（${name || '未知'}）`, '刷新页面重试')
    }
    return
  }

  let v: HTMLVideoElement | undefined
  for (let i = 0; i < 20 && !v; i++) {
    v = video.value
    if (!v) await new Promise((r) => setTimeout(r, 50))
  }
  if (!v) {
    fail('画面区域未就绪', '请关闭弹窗后重新点一次')
    return
  }

  v.muted = true
  v.setAttribute('playsinline', 'true')
  v.setAttribute('webkit-playsinline', 'true')
  v.srcObject = stream
  try {
    await v.play()
  } catch {
    fail('视频预览被浏览器拦截', '点「重新尝试」或刷新页面')
    return
  }
  await new Promise<void>((resolve) => {
    if (v!.readyState >= 2 && v!.videoWidth > 0) return resolve()
    const done = () => resolve()
    v!.addEventListener('loadeddata', done, { once: true })
    setTimeout(done, 3000)
  })
  if (!v.videoWidth) {
    fail('摄像头已授权，但没有收到画面', '检查系统相机权限，或关掉占用相机的 App')
    return
  }

  // 摄像头就绪后再加载模型（首次约 5~15 秒，之后走缓存）
  hint.value = '正在加载活体模型…'
  const ok = await loadLandmarker()
  compat.value = !ok
  action.value = Math.random() < 0.5 ? 'blink' : 'mouth'
  stage.value = 'action'
  hint.value = ok ? (action.value === 'blink' ? '请眨一下眼' : '请张一下嘴') : '请眨一下眼（兼容模式）'
  loop()

  timer = window.setTimeout(() => {
    if (stage.value === 'action') {
      fail('没有检测到动作', '把脸放框内、光线亮一点，然后重新尝试')
    }
  }, 20000)
}

// ---------------- 检测循环 ----------------
function loop() {
  const v = video.value
  if (!v || !stream) return
  if (v.readyState >= 2) {
    if (compat.value || !landmarker) motionStep(v)
    else faceStep(v)
  }
  raf = requestAnimationFrame(loop)
}

function faceStep(v: HTMLVideoElement) {
  const ts = performance.now()
  if (ts - lastTs < 33) return
  lastTs = ts

  let res
  try {
    res = landmarker!.detectForVideo(v, ts)
  } catch {
    return
  }
  const cats = res.faceBlendshapes?.[0]?.categories
  if (!cats?.length) {
    hint.value = '没看到人脸，请正对屏幕'
    return
  }
  const score = (name: string) => cats.find((c) => c.categoryName === name)?.score ?? 0

  if (action.value === 'blink') {
    const blink = Math.max(score('eyeBlinkLeft'), score('eyeBlinkRight'))
    hint.value = phase === 'waitRest' ? '请眨一下眼' : '保持一下…'
    if (phase === 'waitRest') {
      if (blink < 0.25) phase = 'waitAction' // 先确认睁眼
    } else if (blink > 0.55) {
      hint.value = '检测到眨眼…'
      window.setTimeout(finish, 120)
    }
  } else {
    const jaw = score('jawOpen')
    hint.value = phase === 'waitRest' ? '请张一下嘴' : '保持一下…'
    if (phase === 'waitRest') {
      if (jaw < 0.15) phase = 'waitAction'
    } else if (jaw > 0.45) {
      hint.value = '检测到张嘴…'
      window.setTimeout(finish, 120)
    }
  }
}

function motionStep(v: HTMLVideoElement) {
  probeCtx.drawImage(v, 0, 0, probe.width, probe.height)
  const data = probeCtx.getImageData(0, 0, probe.width, probe.height).data
  if (prev) {
    let diff = 0
    let count = 0
    for (let y = 20; y < 46; y++) {
      for (let x = 20; x < 76; x++) {
        const i = (y * probe.width + x) * 4
        diff += Math.abs(data[i] - prev[i])
        count++
      }
    }
    const mean = diff / count
    const now = performance.now()
    if (mean > 10 && now - lastMotionAt > 200) {
      motions++
      lastMotionAt = now
      hint.value = `检测到动作 ${Math.min(motions, 3)}/3`
      if (motions >= 3) {
        window.setTimeout(finish, 200)
        return
      }
    }
  }
  prev = new Uint8ClampedArray(data)
}

// ---------------- 连拍 3 帧 ----------------
function grab(v: HTMLVideoElement): string {
  const w = Math.min(v.videoWidth || 640, 640)
  const h = Math.round((w * (v.videoHeight || 960)) / (v.videoWidth || 640))
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')!
  ctx.translate(w, 0)
  ctx.scale(-1, 1) // 镜像，和预览一致
  ctx.drawImage(v, 0, 0, w, h)
  return c.toDataURL('image/jpeg', 0.75)
}

async function finish() {
  if (stage.value === 'passed') return
  stage.value = 'passed'
  hint.value = '核验通过，正在采集…'
  const v = video.value
  if (!v) return
  const frames: string[] = []
  for (let i = 0; i < 3; i++) {
    frames.push(grab(v))
    if (i < 2) await new Promise((r) => setTimeout(r, 140))
  }
  stopCamera()
  visible.value = false
  emit('captured', frames)
}

function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const img = new Image()
    img.onload = () => {
      const w = Math.min(img.width, 640)
      const h = Math.round((w * img.height) / img.width)
      const c = document.createElement('canvas')
      c.width = w
      c.height = h
      c.getContext('2d')!.drawImage(img, 0, 0, w, h)
      const one = c.toDataURL('image/jpeg', 0.8)
      stopCamera()
      visible.value = false
      emit('captured', [one])
    }
    img.src = String(reader.result)
  }
  reader.readAsDataURL(file)
}

function retry() {
  stopCamera()
  phase = 'waitRest'
  nextTick(start)
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      phase = 'waitRest'
      nextTick(start)
    } else {
      stopCamera()
    }
  },
)
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title || '人脸核验'"
    width="92%"
    style="max-width: 420px; border-radius: 14px"
    align-center
    :close-on-click-modal="false"
    @closed="stopCamera"
  >
    <div class="face-stage">
      <div class="face-frame" :class="{ ok: stage === 'passed' }">
        <video ref="video" class="face-video" playsinline muted autoplay />
        <div v-if="stage === 'loading' || stage === 'error' || stage === 'unsupported'" class="face-mask">
          <span>{{ stage === 'loading' ? '加载中…' : '摄像头未开启' }}</span>
        </div>
        <div class="face-oval" />
      </div>

      <p class="face-hint">{{ hint }}</p>
      <p v-if="compat" class="face-guide">兼容模式：动作检测精度较低，建议用 Chrome / Safari</p>
      <p v-if="errorMsg" class="face-error">{{ errorMsg }}</p>
      <p v-if="guide" class="face-guide">{{ guide }}</p>
      <p v-if="tip" class="face-tip">{{ tip }}</p>

      <div class="face-actions">
        <el-button
          v-if="stage === 'error' || stage === 'unsupported'"
          type="success"
          @click="retry"
        >
          重新尝试
        </el-button>
        <el-button v-else-if="stage === 'action'" text size="small" @click="retry">
          重开摄像头
        </el-button>
        <el-button v-if="allowUpload" text @click="fileInput?.click()">
          无法使用摄像头？上传照片
        </el-button>
      </div>
      <input
        v-if="allowUpload"
        ref="fileInput"
        type="file"
        accept="image/*"
        capture="user"
        hidden
        @change="onFile"
      />
    </div>
  </el-dialog>
</template>

<style scoped>
.face-stage { text-align: center; }
.face-frame {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  max-height: 46vh;
  border-radius: 16px;
  overflow: hidden;
  background: #1d2530;
}
.face-frame.ok { box-shadow: 0 0 0 3px #67c23a inset; }
/* 不要给 video 加 transform，iOS Safari 弹窗里对带 transform 的 video 有合成 bug */
.face-video { width: 100%; height: 100%; object-fit: cover; }
.face-mask {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: #c0c4cc; font-size: 13px; background: rgba(20, 24, 30, 0.85);
}
.face-oval {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 62%; height: 74%; border: 2px dashed rgba(103, 194, 58, 0.9); border-radius: 50%;
  pointer-events: none;
}
.face-hint { margin: 12px 0 4px; font-size: 14px; color: #303133; }
.face-error { margin: 4px 0; font-size: 13px; color: #f56c6c; line-height: 1.5; font-weight: 500; }
.face-guide { margin: 4px 0; font-size: 12px; color: #909399; line-height: 1.6; }
.face-tip { margin: 4px 0; font-size: 12px; color: #909399; line-height: 1.5; }
.face-actions { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; align-items: center; }
</style>
