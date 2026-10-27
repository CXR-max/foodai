<script setup lang="ts">
// 注册页（注册成功自动登录）
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '', confirm: '', nickname: '' })

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 50, message: '用户名 2~50 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    {
      validator: (_r: any, v: string, cb: any) =>
        v === form.password ? cb() : cb(new Error('两次密码不一致')),
      trigger: 'blur',
    },
  ],
}

async function submit() {
  await formRef.value?.validate()
  loading.value = true
  try {
    await auth.register(form.username, form.password, form.nickname)
    ElMessage.success('注册成功，欢迎使用！')
    router.push('/dashboard')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <el-card class="auth-card">
      <h2 class="auth-title">注册账号</h2>
      <p class="auth-sub">开启你的个性化健康之旅</p>
      <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="submit">
        <el-form-item prop="username"><el-input v-model="form.username" placeholder="用户名" /></el-form-item>
        <el-form-item prop="nickname"><el-input v-model="form.nickname" placeholder="昵称（可选，排行榜展示）" /></el-form-item>
        <el-form-item prop="password"><el-input v-model="form.password" type="password" show-password placeholder="密码（至少6位）" /></el-form-item>
        <el-form-item prop="confirm"><el-input v-model="form.confirm" type="password" show-password placeholder="确认密码" /></el-form-item>
        <el-button type="success" size="large" style="width: 100%" :loading="loading" @click="submit">注 册</el-button>
      </el-form>
      <div class="auth-footer">已有账号？<router-link to="/login">去登录</router-link></div>
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
