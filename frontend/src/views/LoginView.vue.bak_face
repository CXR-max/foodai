<script setup lang="ts">
// 登录页
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  await formRef.value?.validate()
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <el-card class="auth-card">
      <h2 class="auth-title">🥗 识膳智行</h2>
      <p class="auth-sub">AI 健康管理平台 · 爱吃才是最好的营养师</p>
      <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="submit">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="密码" />
        </el-form-item>
        <el-button type="success" size="large" style="width: 100%" :loading="loading" @click="submit">
          登 录
        </el-button>
      </el-form>
      <div class="auth-footer">
        还没有账号？<router-link to="/register">立即注册</router-link>
      </div>
    </el-card>
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
.auth-footer { text-align: center; margin-top: 16px; font-size: 13px; color: #909399; }
</style>
