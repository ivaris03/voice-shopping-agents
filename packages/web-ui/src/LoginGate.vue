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
  customer: { phone: '13700000001', password: '12345678' },
  merchant: { phone: '13800000001', password: '12345678' },
  platform: { phone: '13900000001', password: '12345678' },
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
    <section class="auth-gate__brand-panel" aria-label="声选导购">
      <div class="auth-gate__brand-lockup">
        <span class="auth-gate__monogram" aria-hidden="true">声</span>
        <span>声选</span>
      </div>
      <div class="auth-gate__brand-copy">
        <p class="auth-gate__brand-kicker">VOICE SHOPPING</p>
        <h2>让每一次选择，<br />更从容一点。</h2>
        <p>留一点时间给自己，也留一点空间给真正合适的答案。</p>
      </div>
      <p class="auth-gate__brand-footer">VOICE SHOPPING / 2026</p>
    </section>

    <section class="auth-gate__login-region" aria-labelledby="login-title">
      <div class="auth-gate__login-content">
        <div class="auth-gate__login-heading">
          <p class="auth-gate__eyebrow">欢迎回来</p>
          <h1 id="login-title">登录{{ workspaceName }}</h1>
          <p>继续从你上次停下的地方出发。</p>
        </div>

        <p v-if="loading" class="auth-gate__status">正在恢复登录状态...</p>
        <form v-else class="auth-gate__form" @submit.prevent="submit">
          <label class="auth-gate__field">
            <span>手机号</span>
            <input v-model="phone" type="tel" autocomplete="tel" inputmode="numeric" required />
          </label>
          <label class="auth-gate__field">
            <span>密码</span>
            <input v-model="password" type="password" autocomplete="current-password" required />
          </label>
          <p v-if="error" class="auth-gate__error" role="alert">{{ error }}</p>
          <button type="submit" :disabled="submitting">
            {{ submitting ? '登录中...' : '登录' }}
          </button>
        </form>
      </div>
    </section>
  </main>
</template>

<style scoped>
.auth-gate {
  width: 100%;
  min-height: 100dvh;
  max-width: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: minmax(360px, 0.92fr) minmax(460px, 1.08fr);
  color: #1c211d;
  background: #f7f7f3;
}

.auth-gate__brand-panel {
  position: relative;
  min-height: 100dvh;
  display: grid;
  grid-template-rows: auto 1fr auto;
  overflow: hidden;
  padding: 46px clamp(38px, 6vw, 88px);
  color: #f6f4ee;
  background: #1f2d25;
}

.auth-gate__brand-panel::before {
  position: absolute;
  inset: 24px;
  border: 1px solid rgb(246 244 238 / 14%);
  content: '';
  pointer-events: none;
}

.auth-gate__brand-lockup,
.auth-gate__brand-copy,
.auth-gate__brand-footer {
  position: relative;
  z-index: 1;
}

.auth-gate__brand-lockup {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  width: fit-content;
  font-family: Georgia, 'Songti SC', serif;
  font-size: 20px;
  line-height: 1;
}

.auth-gate__monogram {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  color: #1f2d25;
  background: #ee6a45;
  font-family: Inter, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  font-weight: 800;
}

.auth-gate__brand-copy {
  align-self: center;
  max-width: 490px;
  padding: 56px 0;
}

.auth-gate__brand-kicker,
.auth-gate__brand-footer {
  margin: 0;
  color: rgb(246 244 238 / 62%);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.auth-gate__brand-copy h2 {
  margin: 20px 0 0;
  color: #f6f4ee;
  font-family: Georgia, 'Songti SC', serif;
  font-size: 58px;
  font-weight: 400;
  letter-spacing: 0;
  line-height: 1.08;
}

.auth-gate__brand-copy > p:last-child {
  max-width: 350px;
  margin: 25px 0 0;
  color: rgb(246 244 238 / 68%);
  font-size: 15px;
  line-height: 1.7;
}

.auth-gate__login-region {
  display: grid;
  min-height: 100dvh;
  align-items: center;
  padding: 56px clamp(40px, 10vw, 160px);
  background: #f7f7f3;
}

.auth-gate__login-content {
  width: min(100%, 408px);
  margin: 0 auto;
}

.auth-gate__login-heading {
  margin-bottom: 42px;
}

.auth-gate__eyebrow {
  margin: 0 0 14px;
  color: #b94e31;
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0.1em;
}

.auth-gate__login-heading h1 {
  max-width: none;
  margin: 0;
  color: #1c211d;
  font-family: Georgia, 'Songti SC', serif;
  font-size: 42px;
  font-weight: 400;
  letter-spacing: 0;
  line-height: 1.12;
}

.auth-gate__login-heading > p:last-child {
  margin: 14px 0 0;
  color: #697069;
  font-size: 15px;
  line-height: 1.65;
}

.auth-gate__status {
  margin: 0;
  color: #697069;
  font-size: 15px;
  line-height: 1.6;
}

.auth-gate__form {
  display: grid;
  gap: 22px;
}

.auth-gate__field {
  display: grid;
  gap: 9px;
  color: #303630;
  font-size: 13px;
  font-weight: 700;
}

input {
  width: 100%;
  box-sizing: border-box;
  min-height: 52px;
  border: 1px solid #bec4bc;
  border-radius: 4px;
  padding: 12px 14px;
  color: #1c211d;
  background: #ffffff;
  font: inherit;
  transition: border-color 150ms ease, box-shadow 150ms ease;
}

input:focus {
  outline: 0;
  border-color: #1f2d25;
  box-shadow: 0 0 0 3px rgb(185 78 49 / 16%);
}

button {
  min-height: 52px;
  border: 0;
  border-radius: 4px;
  color: #f7f7f3;
  background: #1f2d25;
  cursor: pointer;
  font: inherit;
  font-size: 15px;
  font-weight: 750;
  transition: background 150ms ease, transform 150ms ease;
}

button:hover:not(:disabled) {
  background: #314238;
  transform: translateY(-1px);
}

button:focus-visible {
  outline: 3px solid #ee6a45;
  outline-offset: 3px;
}

button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.auth-gate__error {
  margin: -4px 0 0;
  padding-left: 12px;
  border-left: 2px solid #c84d32;
  color: #9f3825;
  font-size: 13px;
  line-height: 1.55;
}

@media (max-width: 900px) {
  .auth-gate {
    grid-template-columns: 1fr;
  }

  .auth-gate__brand-panel,
  .auth-gate__login-region {
    min-height: auto;
  }

  .auth-gate__brand-panel {
    min-height: 330px;
    padding: 30px 38px;
  }

  .auth-gate__brand-copy {
    padding: 38px 0;
  }

  .auth-gate__brand-copy h2 {
    font-size: 46px;
  }

  .auth-gate__login-region {
    padding: 56px 38px 64px;
  }
}

@media (max-width: 520px) {
  .auth-gate__brand-panel {
    min-height: 294px;
    padding: 25px 24px;
  }

  .auth-gate__brand-panel::before {
    inset: 14px;
  }

  .auth-gate__brand-copy {
    padding: 28px 0;
  }

  .auth-gate__brand-copy h2 {
    font-size: 37px;
  }

  .auth-gate__brand-copy > p:last-child {
    margin-top: 16px;
    font-size: 14px;
  }

  .auth-gate__brand-footer {
    font-size: 10px;
  }

  .auth-gate__login-region {
    padding: 44px 24px 52px;
  }

  .auth-gate__login-heading {
    margin-bottom: 32px;
  }

  .auth-gate__login-heading h1 {
    font-size: 34px;
  }

  .auth-gate__form {
    gap: 18px;
  }
}
</style>
