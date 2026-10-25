<script setup lang="ts">
defineProps<{ busy?: boolean }>();
</script>
<template>
  <img
    class="siva-avatar"
    src="/mascot/siva.png"
    alt="SIVAX Agent, your SIVAX assistant"
    width="80"
    height="80"
  />
</template>
<style scoped>
.siva-avatar {
  width: 80px;
  height: 80px;
  object-fit: contain;
  flex-shrink: 0;
  filter: drop-shadow(0 5px 10px #19365312);
}
@media (max-width: 600px) {
  .siva-avatar {
    width: 60px;
    height: 60px;
  }
}
</style>
