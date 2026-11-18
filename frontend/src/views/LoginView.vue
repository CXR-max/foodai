<script setup lang="ts">
// 登录页：账号密码登录 + 人脸登录
import { defineAsyncComponent, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { homePath } from '@/router'
import * as faceApi from '@/api/face'

// 人脸组件内部依赖 MediaPipe（约 20MB WASM），静态引入会把整个登录页首屏拖住
// 改成点击「人脸登录」时才按需加载，账号密码登录不受影响
const FaceCaptureDialog = defineAsyncComponent(() => import('@/components/FaceCaptureDialog.vue'))

const auth = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const faceVisible = ref(false)
const faceLoading = ref(false)
const faceMock = ref(false)

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  if (loading.value) return
  await formRef.value?.validate()
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    // 手机自带浏览器 / 内网穿透下 history 路由偶发不跳转；
    // 登录后整页进入对应首页最稳（token 已写 localStorage，守卫会放行）。
    window.location.replace(homePath())
  } finally {
    loading.value = false
  }
}

/** 点人脸登录：先确认这个账号绑过脸，再唤起摄像头 */
async function openFace() {
  if (!form.username.trim()) {
    ElMessage.warning('请先输入用户名，再刷脸登录')
    return
  }
  faceLoading.value = true
  try {
    const s = await faceApi.faceStatus(form.username.trim())
    faceMock.value = s.mock
    if (!s.registered) {
      ElMessage.warning('该账号还没绑定人脸，请先用密码登录，在「我的」里绑定')
      return
    }
    faceVisible.value = true
  } catch {
    /* http.ts 已经统一提示过错误 */
  } finally {
    faceLoading.value = false
  }
}

/** 拿到现场照 → 1:1 比对 → ticket → 换 token */
async function onFaceCaptured(images: string[]) {
  faceLoading.value = true
  try {
    const { ticket } = await faceApi.faceVerify(form.username.trim(), images)
    const data = await faceApi.faceLogin(ticket)
    auth.setSession(data.token, data.user)
    ElMessage.success('刷脸登录成功')
    window.location.replace(homePath())
  } catch {
    /* 错误提示已统一处理 */
  } finally {
    faceLoading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <el-card class="auth-card">
      <h2 class="auth-title">🥗 识膳智行</h2>
      <p class="auth-sub">AI 健康管理平台 · 爱吃才是最好的营养师</p>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        size="large"
        @submit.prevent="submit"
        @keyup.enter.prevent="submit"
      >
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="密码" />
        </el-form-item>
        <el-button
          type="success"
          size="large"
          style="width: 100%"
          native-type="button"
          :loading="loading"
          @click="submit"
        >
          登 录
        </el-button>
      </el-form>

      <el-divider class="auth-divider">或</el-divider>

      <el-button class="face-btn" size="large" style="width: 100%" :loading="faceLoading" @click="openFace">
        👤 人脸登录
      </el-button>
      <p class="face-note">
        首次使用需先用密码登录，在「我的」里绑定人脸
        <el-tag v-if="faceMock" size="small" type="info" effect="plain" class="mock-tag">演示模式</el-tag>
      </p>

      <div class="auth-footer">
        还没有账号？<router-link to="/register">立即注册</router-link>
      </div>
    </el-card>

    <FaceCaptureDialog
      v-model:visible="faceVisible"
      title="人脸登录"
      :tip="faceMock ? '当前为演示模式，未做真实人脸比对' : ''"
      @captured="onFaceCaptured"
    />
  </div>
</template>

<style scoped>
.auth-page {
  height: 100%; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #1d2530 0%, #2c3a2f 100%);
}
.auth-card { width: 380px; padding: 12px 8px; }
.auth-title { text-align: center; margin: 8px 0 4px; }
.auth-sub { text-align: center; color: #909399; font-size: 13px; margin-bottom: 24px; }
.auth-divider { margin: 18px 0 14px; }
.face-btn { border-color: #67c23a; color: #529b2e; background: #f0f9eb; }
.face-note { margin: 10px 0 0; font-size: 11px; color: #a8abb2; text-align: center; line-height: 1.6; }
.mock-tag { margin-left: 4px; }
.auth-footer { text-align: center; margin-top: 16px; font-size: 13px; color: #909399; }
</style>
