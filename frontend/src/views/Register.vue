<template>
  <div class="register-wrapper">
    <div class="register-container">
      <div class="header">
        <h1>N2Sys</h1>
        <p>Create your account</p>
      </div>
      <el-form
        ref="formRef"
        :model="user"
        :rules="rules"
        status-icon
        label-position="left"
        label-width="180px"
      >
        <el-form-item prop="username" label="Username">
          <el-input v-model="user.username" placeholder="It is used for sign in, e.g. JeremyGuo" />
        </el-form-item>
        <el-form-item prop="account_name" label="Account Name">
          <el-input v-model="user.account_name" placeholder="It is used for Linux account name, e.g. guojunyi" />
        </el-form-item>
        <el-form-item prop="realname" label="Real Name">
          <el-input v-model="user.realname" placeholder="It is your real name, e.g. 张三" />
        </el-form-item>
        <el-form-item prop="mail" label="Email">
          <el-input v-model="user.mail" placeholder="It is for notification receiving." />
        </el-form-item>
        <el-form-item prop="password" label="Password">
          <el-input type="password" v-model="user.password" placeholder="Password" />
        </el-form-item>
        <el-form-item prop="confirm_password" label="Confirm Password">
          <el-input type="password" v-model="confirmPassword" placeholder="Confirm Password" />
        </el-form-item>
        <el-form-item label="Public Key">
          <el-input type="textarea" v-model="user.public_key" placeholder="Public Key" rows="4" />
        </el-form-item>
      </el-form>
      <div class="footer">
        <el-button type="primary" class="full-width" @click="register">Sign up</el-button>
        <p>Already have an account? <router-link to="/login">Sign in</router-link></p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';

const router = useRouter();
const formRef = ref(null);
const user = reactive({ username: '', account_name: '', realname: '', mail: '', password: '', public_key: '' });
const confirmPassword = ref('');

const rules = {
  username: [{ required: true, message: 'Please enter your username', trigger: 'blur' }],
  account_name: [{ required: true, message: 'Please enter your account name', trigger: 'blur' }],
  realname: [{ required: true, message: 'Please enter your real name', trigger: 'blur' }],
  mail: [
    { required: true, message: 'Please enter your email', trigger: 'blur' },
    { type: 'email', message: 'Please enter a valid email address', trigger: ['blur', 'change'] }
  ],
  password: [
    { required: true, message: 'Please enter a password', trigger: 'blur' },
    { min: 8, message: 'Password must be at least 8 characters', trigger: 'blur' }
  ],
  confirm_password: [
  ]
};

function register() {
  formRef.value.validate(async valid => {
    if (!valid) {
      ElMessage.error('Please fill out the form correctly');
      return;
    }
    if (user.password !== confirmPassword.value) {
      ElMessage.error('Passwords do not match');
      return;
    }
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(user)
      });
      if (res.ok) {
        router.push({ name: 'Login' });
      } else {
        const data = await res.json();
        ElMessage.error(data.msg || 'Registration failed');
      }
    } catch (err) {
      ElMessage.error('Network error');
    }
  });
}
</script>

<style scoped>
.register-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  /* background-color: #f7f7f7; */
}
.register-container {
  width: 600px;
  padding: 20px;
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.1);
}
.header {
  text-align: center;
  margin-bottom: 30px;
}
.header h1 {
  margin: 0;
  font-size: 24px;
  font-weight: bold;
}
.header p {
  margin: 5px 0 0;
  color: #6a737d;
}
.el-form-item {
  margin-bottom: 20px;
}
.full-width {
  width: 100%;
}
.footer {
  text-align: center;
  margin-top: 20px;
}
.footer a {
  color: #0366d6;
}
</style>
