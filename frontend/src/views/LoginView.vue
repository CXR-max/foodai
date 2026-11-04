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
    <div class="auth-shell">
      <aside class="auth-brand">
        <div class="ab-top mono">SHISHAN ZHIXING · EST. 2026</div>
        <div>
          <div class="ab-logo">膳</div>
          <h1 class="ab-title">识膳<br /><em>智行</em></h1>
          <p class="ab-sub">AI 健康管理平台</p>
        </div>
        <ul class="ab-list">
          <li><span class="no mono">01</span>食物识别与营养换算</li>
          <li><span class="no mono">02</span>按目标生成每日餐单</li>
          <li><span class="no mono">03</span>打卡 / 积分 / 排行榜</li>
        </ul>
        <div class="ab-foot mono">EAT WELL · LIVE WELL</div>
      </aside>

      <section class="auth-form">
        <h2 class="auth-title">登录</h2>
        <p class="auth-sub">继续你的每日健康报告。</p>

        <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="submit">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名" />
          </el-form-item>
          <el-form-item prop="password">
            <el-input v-model="form.password" type="password" show-password placeholder="密码" />
          </el-form-item>
          <el-button type="primary" size="large" style="width: 100%; margin-top: 6px" :loading="loading" @click="submit">
            登 录
          </el-button>
        </el-form>

        <div class="auth-footer">还没有账号？<router-link to="/register">立即注册</router-link></div>
      </section>
    </div>
  </div>
</template>
