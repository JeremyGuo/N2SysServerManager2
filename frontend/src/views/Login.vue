<template>
    <div class="login-wrapper">
        <div class="login-container">
            <div class="header">
            <h1>N2Sys</h1>
            <p>Sign in to your account</p>
            </div>
            <el-form ref="form" :model="user" @submit.native.prevent>
            <el-form-item prop="username">
                <el-input v-model="user.username" placeholder="Username" />
            </el-form-item>
            <el-form-item prop="password">
                <el-input type="password" v-model="user.password" placeholder="Password" />
            </el-form-item>
            <el-form-item>
                <el-button type="primary" @click="signin" class="full-width">Sign in</el-button>
            </el-form-item>
            </el-form>
            <div class="footer">
            <p>New to N2Sys? <router-link to="/register">Sign up</router-link></p>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, reactive } from 'vue';
import { useRouter } from 'vue-router';

const router = useRouter();
const form = ref(null);
const user = reactive({ username: '', password: '' });

async function signin() {
  const formData = new URLSearchParams();
  formData.append('username', user.username);
  formData.append('password', user.password);

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      credentials: 'include',
      body: formData
    });
    if (res.ok) {
      window.location.href = '/';
    } else {
      const data = await res.json();
      console.error(data.detail || 'Sign in failed');
    }
  } catch (err) {
    console.error(err);
  }
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
