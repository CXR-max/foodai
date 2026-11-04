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
    <div class="auth-shell">
      <aside class="auth-brand">
        <div class="ab-top mono">SHISHAN ZHIXING · EST. 2026</div>
        <div>
          <div class="ab-logo">膳</div>
          <h1 class="ab-title">识膳<br /><em>智行</em></h1>
          <p class="ab-sub">AI 健康管理平台</p>
        </div>
        <ul class="ab-list">
          <li><span class="no mono">01</span>建立你的健康档案</li>
          <li><span class="no mono">02</span>获得个性化营养目标</li>
          <li><span class="no mono">03</span>从今天第一餐开始记录</li>
        </ul>
        <div class="ab-foot mono">EAT WELL · LIVE WELL</div>
      </aside>

      <section class="auth-form">
        <h2 class="auth-title">注册</h2>
        <p class="auth-sub">开启你的个性化健康记录。</p>

        <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="submit">
          <el-form-item prop="username"><el-input v-model="form.username" placeholder="用户名" /></el-form-item>
          <el-form-item prop="nickname"><el-input v-model="form.nickname" placeholder="昵称（可选，排行榜展示）" /></el-form-item>
          <el-form-item prop="password"><el-input v-model="form.password" type="password" show-password placeholder="密码（至少 6 位）" /></el-form-item>
          <el-form-item prop="confirm"><el-input v-model="form.confirm" type="password" show-password placeholder="确认密码" /></el-form-item>
          <el-button type="primary" size="large" style="width: 100%; margin-top: 4px" :loading="loading" @click="submit">
            注 册
          </el-button>
        </el-form>

        <div class="auth-footer">已有账号？<router-link to="/login">去登录</router-link></div>
      </section>
    </div>
  </div>
</template>
