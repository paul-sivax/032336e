<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Flame } from "lucide-vue-next";
import MascotFrame from "./MascotFrame.vue";
import { preloadMascot } from "./mascotFrames";
const props = defineProps<{
  mode: "search" | "order";
  status: string;
  phase?: string;
  turbo?: boolean;
  ready: boolean;
}>();
const emit = defineEmits<{ complete: [] }>();
const frame = ref(0),
  elapsed = ref(0),
  reduced = ref(false),
  reportingAt = ref(-1),
  stampingAt = ref(-1),
  presentingAt = ref(-1),
  filledAt = ref(-1),
  actionStartedAt = ref(0);
let timer: ReturnType<typeof setInterval>,
  media: MediaQueryList,
  done = false,
  disposed = false;
// Every illustrated stage stays on screen at least this long, Turbo included.
const STAGE_MS = 2000;
// Covers the bar's 0.8s width transition so 100% is visible before leaving.
const FILL_MS = 900;
const frameMs = computed(() =>
  props.turbo && props.mode === "search" ? 140 : 240,
);
const completed = computed(() => props.status === "completed" && props.ready);
const stage = computed(() => {
  if (props.mode === "order") {
    if (elapsed.value < STAGE_MS && !reduced.value) return "writing";
    // Only hand over the document after delivery and authenticated terms are ready.
    if (
      presentingAt.value >= 0 ||
      (completed.value &&
        stampingAt.value >= 0 &&
        elapsed.value - stampingAt.value >= STAGE_MS)
    )
      return "presenting";
    // Once stamping, stay there even if a progress poll briefly falls back.
    return stampingAt.value >= 0 ||
      ["issuing", "authorizing", "delivering", "complete"].includes(
        props.phase || "",
      ) ||
      completed.value
      ? "stamping"
      : "writing";
  }
  if (elapsed.value < STAGE_MS && !reduced.value) return "writing";
  if (!completed.value || elapsed.value < 2 * STAGE_MS) return "calling";
  return "reporting";
});
const label = computed(() => {
  if (props.status === "payment_unknown") return "Checking your payment";
  if (props.mode === "order")
    return stage.value === "presenting"
      ? "Your term sheet is ready"
      : stage.value === "stamping"
        ? "Issuing your MPT"
        : "Preparing your order";
  return {
    writing: "Putting your plan together",
    calling: "Finding your perfect fit",
    reporting: "Your report is almost ready",
    stamping: "Issuing your MPT",
    presenting: "Your term sheet is ready",
  }[stage.value];
});
const artwork = computed(() =>
  props.turbo && props.mode === "search" && stage.value !== "calling"
    ? "turbo"
    : stage.value,
);
watch(artwork, () => {
  actionStartedAt.value = elapsed.value;
  frame.value = 0;
});
const phaseProgress = computed(() =>
  completed.value && reduced.value
    ? 100
    : props.mode === "order"
      ? stage.value === "presenting"
        ? 100
        : stage.value === "stamping"
          ? props.phase === "complete" || completed.value
            ? 100
            : props.phase === "delivering"
              ? 90
              : 66
          : 24
      : {
          writing: 18,
          calling: 56,
          reporting: 92,
          stamping: 90,
          presenting: 100,
        }[stage.value] || 15,
);
// The bar never moves backwards, whatever order the polled phases arrive in.
const progress = ref(0);
watch(
  phaseProgress,
  (value) => {
    progress.value = Math.max(progress.value, value);
    if (progress.value === 100 && filledAt.value < 0)
      filledAt.value = elapsed.value;
  },
  { immediate: true },
);
function finish() {
  if (!done) {
    done = true;
    emit("complete");
  }
}
function motionChange() {
  reduced.value = media.matches;
}
watch(
  stage,
  (value) => {
    if (value === "reporting") reportingAt.value = elapsed.value;
    if (value === "stamping") stampingAt.value = elapsed.value;
    if (value === "presenting") presentingAt.value = elapsed.value;
  },
  { immediate: true },
);
watch([elapsed, completed, reduced], () => {
  if (!completed.value) return;
  if (reduced.value) {
    finish();
    return;
  }
  if (
    props.mode === "search" &&
    reportingAt.value >= 0 &&
    elapsed.value - reportingAt.value >= STAGE_MS
  )
    finish();
  if (
    props.mode === "order" &&
    presentingAt.value >= 0 &&
    elapsed.value - presentingAt.value >= STAGE_MS &&
    filledAt.value >= 0 &&
    elapsed.value - filledAt.value >= FILL_MS
  )
    finish();
});
onMounted(async () => {
  media = window.matchMedia("(prefers-reduced-motion: reduce)");
  motionChange();
  media.addEventListener("change", motionChange);
  await preloadMascot();
  if (disposed) return;
  const start = performance.now();
  timer = setInterval(() => {
    elapsed.value = performance.now() - start;
    if (!reduced.value)
      frame.value =
        stage.value === "presenting"
          ? 0
          : Math.floor(
              (elapsed.value - actionStartedAt.value) / frameMs.value,
            ) % 4;
  }, 60);
});
onUnmounted(() => {
  disposed = true;
  clearInterval(timer);
  media?.removeEventListener("change", motionChange);
});
</script>
<template>
  <div
    class="workflow"
    :class="{ 'turbo-workflow': turbo && mode === 'search' }"
    :data-stage="stage"
    :data-mode="mode"
  >
    <div class="work-art">
      <div class="work-halo"></div>
      <div
        v-if="turbo && mode === 'search'"
        class="speed-streaks"
        aria-hidden="true"
      >
        <i></i><i></i><i></i>
      </div>
      <MascotFrame
        :action="artwork"
        :frame="reduced ? 0 : frame"
        :label="
          stage === 'presenting'
            ? 'SIVAX Agent holding up your completed term sheet'
            : stage === 'stamping'
              ? 'SIVAX Agent stamping your term sheet'
              : stage === 'calling'
                ? 'SIVAX Agent on the phone'
                : 'SIVAX Agent writing your document'
        "
      />
    </div>
    <span class="pill work-label"
      ><Flame v-if="turbo && mode === 'search'" :size="13" />{{
        mode === "order"
          ? "YOUR RECEIPT"
          : turbo
            ? "TURBO"
            : "SIVAX AGENT AT WORK"
      }}</span
    >
    <h2 aria-live="polite">{{ label }}</h2>
    <div
      class="work-progress"
      role="progressbar"
      :aria-label="mode === 'order' ? 'Order progress' : 'Discovery progress'"
      :aria-valuetext="label"
    >
      <div class="work-progress-fill" :style="{ width: progress + '%' }">
        <span></span>
      </div>
    </div>
    <span class="work-status">{{
      status === "payment_unknown"
        ? "Safe to resume"
        : stage === "stamping"
          ? "A little stamp. A big moment."
          : stage === "reporting" || stage === "presenting"
            ? "Made for your plan."
            : "Leave the details to me."
    }}</span>
  </div>
</template>
