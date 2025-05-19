<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
const router = useRouter()
const route = useRoute()

// current user info
const currentUser = ref({ id: null, is_admin: false })

onMounted(async () => {
  const res = await fetch('/api/user/me', { credentials: 'include' })
  if (res.ok) {
    currentUser.value = await res.json()
  }
})

function handleMenuCommand(command) {
  if (command === 'Profile' && currentUser.value.id) {
    router.push({ name: 'Profile', params: { id: currentUser.value.id } })
  } else {
    router.push({ name: command })
  }
}

function handleProfile() {
  if (currentUser.value.id) {
    router.push({ name: 'Profile', params: { id: currentUser.value.id } })
  }
}

function logout() {
  fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
    .then(res => {
      if (res.ok) {
        console.log('Logout successful')
        window.location.href = '/login'
      } else {
        console.error('Logout failed')
      }
    })
    .catch(err => {
      console.error('Network error:', err)
    })
}

// new login method
function login() {
  router.push({ name: 'Login' })
}
</script>

<template>
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
