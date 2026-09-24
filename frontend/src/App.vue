<script setup>
import { ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { apiFetch, reportError } from './api.js'
import { drafts } from './drafts.js'
import ErrorCenter from './components/ErrorCenter.vue'
const router = useRouter()
const route = useRoute()
const currentUser = ref({ id: null, is_admin: false })
let authRequest = 0
watch(() => route.name, async name => {
  if (!name) return
  const request = ++authRequest
  const epoch = drafts.epoch
  try {
    const res = await apiFetch('/api/user/me', {
      credentials: 'include', suppressStatuses: ['Login', 'Register'].includes(name) ? [401] : []
    })
    const user = await res.json()
    if (request === authRequest && epoch === drafts.epoch) {
      // Public-page probes are display-only; only a completed login/protected probe
      // may establish the owner allowed to restore non-secret drafts.
      if (!['Login', 'Register'].includes(name)) drafts.confirmUser(user.id, epoch)
      currentUser.value = user
    }
  } catch (error) {
    if (request === authRequest) currentUser.value = { id: null, is_admin: false }
    reportError(error)
  }
}, { immediate: true })
function handleMenuCommand(command) {
  if (command === 'Profile') handleProfile()
  else router.push({ name: command })
}
function handleProfile() {
  if (currentUser.value.id) router.push({ name: 'Profile', params: { id: currentUser.value.id } })
}
async function logout() {
  await apiFetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  drafts.logout()
  currentUser.value = { id: null, is_admin: false }
  await router.replace({ name: 'Login' })
}
function login() { router.push({ name: 'Login' }) }
</script>

<template>
  <ErrorCenter />
  <el-container style="height: 100vh; width: 100%;">
    <el-header style="display: flex; justify-content: space-between; align-items: center; padding: 0 20px; height: 60px;">
      <el-button type="text" style="font-size: 35px; font-weight: bold; margin-left: 10px;">N2Sys</el-button>
      <el-dropdown @command="handleMenuCommand" trigger="click" popper-append-to-body>
        <el-button type="text" style="font-size: 20px; font-weight: bold; margin-left: 10px;">
          {{ route.name }} <i class="el-icon-arrow-down el-icon--right"></i>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="Summary">Summary</el-dropdown-item>
            <el-dropdown-item command="Servers">Servers</el-dropdown-item>
            <el-dropdown-item v-if="currentUser.id" command="ApplyMachines">申请机器 · Apply Machines</el-dropdown-item>
            <el-dropdown-item command="Devices">Devices</el-dropdown-item>
            <el-dropdown-item command="Management" v-if="currentUser.is_admin">Management</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <div style="display: flex; align-items: center;">
        <template v-if="currentUser.id">
          <el-button class="el-header-button" type="text" @click="handleProfile" style="margin-left: 10px;">Profile</el-button>
          <el-button class="el-header-button" type="text" @click="logout" style="margin-left: 10px;">Logout</el-button>
        </template>
        <template v-else>
          <el-button class="el-header-button" type="text" @click="login" style="margin-left: 10px;">Login</el-button>
        </template>
      </div>
    </el-header>
    <el-main style="margin-top: 70px; height: calc(100vh - 70px); width: 100%; padding: 0;">
      <router-view style="width:100%; height:100%;"/>
    </el-main>
  </el-container>
</template>

<style scoped>
.el-header-button {
  font-size: 16px;
}
.el-header {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  z-index: 1000;
  background: #fff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  overflow: visible;
}
.el-main {
  margin-top: 60px;
  height: calc(100vh - 60px);
  
}
.el-table .fail-row {
  background: oldlace;
}
.el-table .success-row {
  background: #f0f9eb;
}
.el-table .port_index_row {
  background: #f6f6f6;
}
</style>
