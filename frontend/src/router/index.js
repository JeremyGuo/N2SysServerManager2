import { createRouter, createWebHistory } from 'vue-router'
import Summary from '../views/Summary.vue'
import Servers from '../views/Servers.vue'
import Devices from '../views/Devices.vue'
import Management from '../views/Management.vue'
import Login from '../views/Login.vue'
import Register from '../views/Register.vue'
import Profile from '../views/Profile.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/register',
      name: 'Register',
      component: Register
    },
    {
      path: '/login',
      name: 'Login',
      component: Login
    },
    {
      path: '/',
      name: 'Summary',
      component: Summary
    },
    {
      path: '/servers',
      name: 'Servers',
      component: Servers
    },
    {
      path: '/devices',
      name: 'Devices',
      component: Devices
    },
    {
      path: '/management',
      name: 'Management',
      component: Management
    },
    {
      path: '/profile/:id',
      name: 'Profile',
      component: Profile,
      props: true
    }
  ]
})

export default router
