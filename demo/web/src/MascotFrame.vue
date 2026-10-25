<script setup lang="ts">
import { computed } from "vue";
import { mascotPlacement, type MascotAction } from "./mascotFrames";
const props = defineProps<{
  action: MascotAction;
  frame: number;
  label: string;
}>();
const placement = computed(() => mascotPlacement(props.action, props.frame));
</script>

<template>
  <svg
    class="mascot-frame"
    viewBox="0 0 320 320"
    width="280"
    height="280"
    role="img"
    :aria-label="label"
    :data-action="action"
    :data-frame="frame"
    focusable="false"
  >
    <image
      :href="placement.src"
      :x="placement.x"
      :y="placement.y"
      :width="placement.size"
      :height="placement.size"
    />
  </svg>
</template>

<style scoped>
.mascot-frame {
  display: block;
  overflow: hidden;
  aspect-ratio: 1;
}
</style>
