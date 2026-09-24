<template>
    <div class="login-wrapper">
        <div class="login-container">
            <div class="header">
            <h1>N2Sys</h1>
            <p>Sign in to your account</p>
            </div>
            <el-alert v-if="route.query.expired === '1'" title="Your session has expired. Please sign in again. Non-secret Profile/application drafts can be restored only for the same account in this tab." type="warning" :closable="false" show-icon />
            <el-form ref="form" :model="user" @submit.prevent="signin">
            <el-form-item prop="username">
                <el-input v-model="user.username" placeholder="Username" />
            </el-form-item>
            <el-form-item prop="password">
                <el-input type="password" v-model="user.password" placeholder="Password" />
            </el-form-item>
            <el-form-item>
                <el-button type="primary" @click="signin" :loading="loading" class="full-width">Sign in</el-button>
            </el-form-item>
            </el-form>
            <div class="footer">
            <p>New to N2Sys? <router-link to="/register">Sign up</router-link></p>
            </div>
        </div>
    </div>
</template>

<script setup>
import { apiFetch, reportError } from '../api.js'
import { ref, reactive, onBeforeUnmount } from 'vue';
import { useRouter, useRoute } from 'vue-router'
import { safeRedirect } from '../authNavigation.js'
import { drafts } from '../drafts.js'
const router = useRouter()
const route = useRoute()
const form = ref(null);
const loading = ref(false);
const user = reactive({ username: '', password: '' });
onBeforeUnmount(() => { user.password = '' })

async function signin() {
  if (loading.value) return;
  loading.value = true;
  const formData = new URLSearchParams();
  formData.append('username', user.username);
  formData.append('password', user.password);

  try {
    await apiFetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      credentials: 'include',
      body: formData
    });
    user.password = ''
    const epoch = drafts.epoch
    const me = await (await apiFetch('/api/user/me', { credentials: 'include', suppressStatuses: [401] })).json()
    if (!drafts.confirmUser(me.id, epoch)) return
    await router.replace(safeRedirect(route.query.redirect));
  } catch (err) {
    reportError(err);
  } finally { user.password = ''; loading.value = false; }
}
</script>

<style scoped>
 .login-wrapper {
   display: flex;
   justify-content: center;
   align-items: center;
   height: 100vh;
 }

 .login-container {
   width: 360px;
   padding: 20px;
   background-color: #fff;
   border-radius: 8px;
   box-shadow: 0 1px 5px rgba(0, 0, 0, 0.1);
   text-align: center;
 }

.header h1 {
   margin: 0;
   font-size: 24px;
   font-weight: bold;
}
.header p {
   margin: 5px 0 20px;
   color: #6a737d;
}

.el-form-item {
   margin-bottom: 20px;
}
.full-width {
   width: 100%;
}
.footer {
   margin-top: 20px;
}
.footer a {
   color: #0366d6;
}
</style>
