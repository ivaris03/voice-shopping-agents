<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  clearAccessToken,
  getAccessToken,
  getCurrentUser,
  login,
  type AuthenticatedUser,
  type UserRole,
} from './api'

const props = defineProps<{
  requiredRole: UserRole
  workspaceName: string
}>()

const emit = defineEmits<{
  authenticated: [user: AuthenticatedUser]
}>()

const defaultCredentialsByRole: Record<UserRole, { phone: string; password: string }> = {
  customer: { phone: '13900000101', password: '12345678' },
  merchant: { phone: '13800000002', password: '12345678' },
  platform: { phone: '13800000001', password: '12345678' },
}
const defaultCredentials = defaultCredentialsByRole[props.requiredRole]
const phone = ref(defaultCredentials.phone)
const password = ref(defaultCredentials.password)
const loading = ref(true)
const submitting = ref(false)
const error = ref('')

function acceptUser(user: AuthenticatedUser) {
  if (user.role !== props.requiredRole) {
    clearAccessToken()
    error.value = `该账号无权进入${props.workspaceName}`
    return
  }
  emit('authenticated', user)
}

async function restoreSession() {
  if (!getAccessToken()) {
    loading.value = false
    return
  }
  try {
    acceptUser(await getCurrentUser())
  } catch {
    clearAccessToken()
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!phone.value.trim() || !password.value) return
  submitting.value = true
  error.value = ''
  try {
    acceptUser(await login(phone.value.trim(), password.value))
  } catch (reason) {
    clearAccessToken()
    error.value = reason instanceof Error ? reason.message : '登录失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

onMounted(() => void restoreSession())
</script>

<template>
  <main class="auth-gate">
    <section class="auth-gate__panel" aria-labelledby="login-title">
      <div class="auth-gate__brand" aria-hidden="true">声</div>
      <p class="auth-gate__eyebrow">声选导购</p>
      <h1 id="login-title">{{ workspaceName }}</h1>
      <p class="auth-gate__copy">使用已有账号登录后继续。</p>
      <p v-if="loading" class="auth-gate__status">正在恢复登录状态...</p>
      <form v-else class="auth-gate__form" @submit.prevent="submit">
        <label>
          手机号
          <input v-model="phone" type="tel" autocomplete="tel" inputmode="numeric" required />
        </label>
        <label>
          密码
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <p v-if="error" class="auth-gate__error" role="alert">{{ error }}</p>
        <button type="submit" :disabled="submitting">
          {{ submitting ? '登录中...' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.auth-gate {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #f2f5f7;
  color: #15202b;
}

.auth-gate__panel {
  width: min(100%, 400px);
  padding: 32px;
  border: 1px solid #cbd5dd;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 16px 35px rgb(25 45 60 / 10%);
}

.auth-gate__brand {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 8px;
  background: #0f766e;
  color: #ffffff;
  font-size: 20px;
  font-weight: 700;
}

.auth-gate__eyebrow {
  margin: 24px 0 6px;
  color: #0f766e;
  font-size: 14px;
  font-weight: 700;
}

h1 {
  margin: 0;
  font-size: 26px;
  line-height: 1.2;
}

.auth-gate__copy,
.auth-gate__status {
  margin: 10px 0 0;
  color: #52616f;
  line-height: 1.6;
}

.auth-gate__form {
  display: grid;
  gap: 16px;
  margin-top: 24px;
}

label {
  display: grid;
  gap: 7px;
  color: #34424f;
  font-size: 14px;
  font-weight: 600;
}

input {
  width: 100%;
  box-sizing: border-box;
  min-height: 42px;
  border: 1px solid #aebbc5;
  border-radius: 6px;
  padding: 9px 10px;
  color: #15202b;
  font: inherit;
}

input:focus {
  outline: 2px solid #14b8a6;
  outline-offset: 1px;
}

button {
  min-height: 42px;
  border: 0;
  border-radius: 6px;
  background: #0f766e;
  color: #ffffff;
  cursor: pointer;
  font: inherit;
  font-weight: 700;
}

button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.auth-gate__error {
  margin: 0;
  color: #b42318;
  font-size: 14px;
  line-height: 1.5;
}
</style>
