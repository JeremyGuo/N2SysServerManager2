<template>
  <aside class="device-chat">
    <el-button v-if="!open" type="primary" round @click="openChat" aria-label="打开设备 AI 聊天">AI · 问问设备</el-button>
    <section v-else class="chat-panel" role="dialog" aria-label="设备 AI 助手">
      <header><strong>设备 AI 助手</strong><div><el-button text :disabled="sending" @click="clearChat">清空</el-button><el-button text @click="open=false" aria-label="收起聊天">收起</el-button></div></header>
      <div class="chat-notice">仅查询设备，不执行操作。发送问题时会将设备清单与 CPU、内存、GPU、磁盘、网卡等硬件快照及采集状态交给管理员配置的 AI 服务；不包含账号凭据。快照可能陈旧或采集失败，不代表实时资源空闲；回答仅供参考。</div>
      <el-alert v-if="!enabled && !checking" :title="statusReason" type="warning" :closable="false" show-icon />
      <div ref="messageList" class="messages" role="log" aria-live="polite">
        <p v-if="!messages.length" class="welcome">可以问：“我们组有哪些设备？”、“有哪些 Mellanox 网卡？”、“哪些机器有 GPU，显存是否已知？”</p>
        <article v-for="(message, index) in messages" :key="index" :class="['message',message.role]">
          <b>{{ message.role === 'user' ? '你' : 'AI' }}</b>
          <div>{{ message.content }}</div>
        </article>
        <p v-if="sending">正在查询设备清单并等待 AI 回答…</p>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <form class="chat-input" @submit.prevent="send">
        <el-input v-model="draft" type="textarea" :rows="2" maxlength="4000" show-word-limit placeholder="询问组内设备（Ctrl/⌘ + Enter 发送）" :disabled="sending" @keydown.ctrl.enter.prevent="send" @keydown.meta.enter.prevent="send" />
        <div class="chat-actions"><el-button text :loading="checking" @click="checkStatus">检查配置</el-button><el-button type="primary" native-type="submit" :loading="sending" :disabled="!enabled || !draft.trim()">发送</el-button></div>
      </form>
    </section>
  </aside>
</template>
<script setup>
import { ref, nextTick } from 'vue'
import { apiFetch, reportError } from '../api'
const open = ref(false)
const enabled = ref(false)
const checking = ref(false)
const statusReason = ref('')
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const error = ref('')
const messageList = ref(null)
async function scrollBottom() { await nextTick(); if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight }
async function checkStatus() {
  checking.value = true
  try {
    const data = await (await apiFetch('/api/ai/status')).json()
    enabled.value = data.enabled
    statusReason.value = data.reason
    error.value = ''
  } catch (e) { enabled.value = false; error.value = e.message; reportError(e) }
  finally { checking.value = false }
}
async function openChat() { open.value = true; await checkStatus(); await scrollBottom() }
function clearChat() { if (!sending.value) { messages.value = []; error.value = '' } }
async function send() {
  const question = draft.value.trim()
  if (!question || sending.value || !enabled.value) return
  const previous = messages.value.slice()
  const next = [...previous, {role:'user', content:question}]
  if (next.length > 20 || next.reduce((n,m) => n + m.content.length, 0) > 20000) {
    error.value = '对话记录过长，请清空对话后再提问。'; return
  }
  messages.value = next
  draft.value = ''
  error.value = ''
  sending.value = true
  await scrollBottom()
  try {
    const response = await apiFetch('/api/ai/chat', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({messages:next}), timeoutMs:130000,
    })
    const result = await response.json()
    messages.value.push({role:'assistant',content:result.answer})
  } catch (e) {
    // Keep the draft so failed questions can be retried, not duplicated in history.
    messages.value = previous
    draft.value = question
    error.value = e.message
    reportError(e)
  } finally { sending.value = false; await scrollBottom() }
}
</script>
<style scoped>
.device-chat {position:fixed;right:24px;bottom:24px;z-index:1800;text-align:left;}
.chat-panel {width:390px;max-width:calc(100vw - 24px);height:580px;max-height:calc(100dvh - 90px);display:flex;flex-direction:column;background:#fff;color:#303133;border:1px solid #dcdfe6;border-radius:14px;box-shadow:0 8px 36px #0003;overflow:hidden;}
header {display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-bottom:1px solid #eee;}
.chat-notice {font-size:12px;padding:10px 14px;background:#f4f7fc;color:#606266;}
.messages {overflow:auto;flex:1;padding:14px;min-height:80px;}
.welcome {color:#909399;}
.message {margin-bottom:12px;padding:10px;border-radius:8px;background:#f3f4f6;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;}
.message.user {background:#ecf5ff;}
.message b {font-size:12px;display:block;margin-bottom:4px;}
.chat-input {padding:10px;border-top:1px solid #eee;}
.chat-actions {display:flex;justify-content:space-between;margin-top:6px;}
@media(max-width:500px) {.device-chat {right:12px;bottom:12px;}}
</style>
