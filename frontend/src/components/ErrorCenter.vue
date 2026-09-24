<script setup>
import { ref, onUnmounted } from 'vue'
import { subscribeErrors, dismissError } from '../api.js'
const errors = ref([])
const unsubscribe = subscribeErrors(value => { errors.value = value })
onUnmounted(unsubscribe)
</script>

<template>
  <aside v-if="errors.length" class="error-center" aria-label="Application errors" aria-live="polite">
    <section v-for="error in errors" :key="error.id" class="error-card" role="alert">
      <button class="error-close" type="button" aria-label="Dismiss error" @click="dismissError(error.id)">×</button>
      <strong>{{ error.status ? `Request failed (HTTP ${error.status})` : 'Operation failed' }}</strong>
      <p>{{ error.message }}</p>
      <small v-if="error.method || error.url">{{ error.method }} {{ error.url }}</small>
      <small v-if="error.context">{{ error.context }}</small>
      <small v-if="error.requestId">Request ID: {{ error.requestId }}</small>
      <small v-if="error.count > 1">Occurred {{ error.count }} times</small>
    </section>
  </aside>
</template>

<style scoped>
.error-center { position: fixed; top: 72px; right: 16px; z-index: 10000; width: min(460px, calc(100vw - 32px)); max-height: calc(100vh - 90px); overflow-y: auto; text-align: left; }
.error-card { position: relative; background: #fff4f4; color: #751818; border: 1px solid #e7a5a5; border-radius: 6px; padding: 16px 38px 16px 16px; margin-bottom: 12px; box-shadow: 0 4px 16px #0002; overflow-wrap: anywhere; }
p { margin: 8px 0; white-space: pre-wrap; } small { display: block; margin-top: 4px; }
.error-close { position: absolute; top: 4px; right: 4px; background: transparent; color: #751818; border: 0; padding: 3px 9px; font-size: 22px; }
</style>
