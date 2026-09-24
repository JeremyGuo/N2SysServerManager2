import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import router from './router'
import { installAuthNavigation } from './authNavigation.js'
import { installGlobalErrorHandlers, reportError } from './api.js'

const app = createApp(App)
installGlobalErrorHandlers(app)
router.onError(error => reportError(error, 'Navigation'))
app.use(ElementPlus)
app.use(router)
installAuthNavigation(router)
app.mount('#app')
