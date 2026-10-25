<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  Copy,
  Download,
  FileText,
  Flame,
  Gauge,
  Home,
  Layers,
  Menu,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Wallet,
  X,
  Zap,
  Bot,
  LockKeyhole,
} from "lucide-vue-next";
import CharacterWorkflow from "./CharacterWorkflow.vue";
import MascotFrame from "./MascotFrame.vue";
import { terms } from "./copy";
import { useDemo } from "./useDemo";
const {
  state,
  config,
  screen,
  busy,
  loading,
  error,
  accessKey,
  term,
  amount,
  currency,
  protection,
  tier,
  catalogPage,
  holdingPage,
  savedPage,
  termsPage,
  termSheet,
  agentInfo,
  flameKey,
  revealTerms,
  workspace,
  search,
  quotes,
  selected,
  intent,
  execution,
  canLeave,
  profile,
  holdings,
  profiles,
  visibleQuotes,
  visibleHoldings,
  visibleProfiles,
  visibleSheetRows,
  sheetRows,
  amountValid,
  feeUnit,
  searchFee,
  orderFee,
  percent,
  money,
  labelTerm,
  name,
  shortDate,
  choiceReady,
  selectionExpired,
  orderPhase,
  isTurbo,
  workflowError,
  workflowStatus,
  start,
  advance,
  chooseProtection,
  chooseTier,
  find,
  openQuote,
  confirm,
  allProducts,
  startRound,
  changeChoices,
  home,
  showTerms,
  reset,
  resume,
  stop,
  menu,
  back,
  connectAgent,
  copyToken,
  downloadTerms,
  completeWorkflow,
} = useDemo();
const step = computed(() =>
  ["term", "amount", "protection"].indexOf(screen.value),
);
const activeWork = computed(() =>
  ["searching", "issuing", "resetting"].includes(screen.value),
);
const agentApproval = computed(
  () =>
    search.value?.state === "input_required" &&
    search.value?.caller !== "browser",
);
const sheet = computed(() => termSheet.value?.terms || {});
const sheetResult = computed(() => termSheet.value?.result || {});
const verified = computed(() => termSheet.value?.source === "validated_ledger");
const screenTitle = computed(
  () =>
    (
      ({
        results: "Your matches",
        catalog: "The collection",
        details: "Your selection",
        terms: "Your term sheet",
        termsDetail: "Term sheet details",
        holdings: "My holdings",
        saved: "Saved plans",
        home: "Your desk",
        speed: "Discovery",
        agent: "Agent connection",
        menu: "Your SIVAX",
        reset: "Reset demo",
        resetting: "A fresh start",
      }) as Record<string, string>
    )[screen.value] || "",
);
function pageMove(
  kind: "catalog" | "holding" | "saved" | "terms",
  delta: number,
) {
  const target = {
    catalog: catalogPage,
    holding: holdingPage,
    saved: savedPage,
    terms: termsPage,
  }[kind];
  target.value += delta;
}
</script>
<template>
  <div class="app-shell" :class="{ 'landing-shell': screen === 'landing' }">
    <header class="topbar">
      <button
        v-if="screen !== 'landing' && !activeWork"
        class="icon-button"
        aria-label="Back"
        :disabled="busy"
        @click="back"
      >
        <ArrowLeft :size="20" />
      </button>
      <img
        v-else
        src="/mascot/siva.png"
        alt=""
        class="brand-avatar"
        width="32"
        height="32"
      />
      <div class="brand">
        <strong>SIVAX <span>Investment Agent</span></strong
        ><small v-if="screen !== 'landing' && screenTitle">{{
          screenTitle
        }}</small>
      </div>
      <span class="network-pill"
        ><i></i
        >{{ config.payment_mode === "xrpl_testnet" ? "TESTNET" : "DEMO" }}</span
      >
      <button
        class="icon-button"
        :aria-label="screen === 'menu' ? 'Close menu' : 'Open menu'"
        :disabled="busy"
        @click="screen === 'menu' ? back() : state ? menu() : (screen = 'auth')"
      >
        <X v-if="screen === 'menu'" :size="19" /><Menu v-else :size="19" />
      </button>
    </header>
    <main :data-screen="screen" class="viewport">
      <div v-if="error" class="error-banner" role="alert">
        <span>{{ error }}</span
        ><button
          class="icon-button"
          aria-label="Dismiss error"
          @click="error = ''"
        >
          <X :size="15" />
        </button>
      </div>
      <section v-if="screen === 'landing'" class="page landing">
        <div class="landing-copy">
          <h1>
            Your Personal<br /><strong>Investment Agent</strong><br />on
            <span>XRPL</span>
          </h1>
        </div>
        <div class="landing-art">
          <div class="landing-orbit"></div>
          <img
            src="/mascot/writing-1.webp"
            alt="SIVAX Investment Agent, ready to find your next investment"
            width="240"
            height="240"
          />
        </div>
        <div class="landing-questions">
          <h2>Just 3 questions</h2>
          <p><span>01</span>How many days?</p>
          <p><span>02</span>How much to invest?</p>
          <p><span>03</span>Safety or higher yield?</p>
        </div>
        <div class="page-actions">
          <button class="primary" :disabled="loading || busy" @click="start">
            {{ busy ? "Starting…" : "Start" }}<ArrowRight :size="19" />
          </button>
        </div>
      </section>
      <section v-else-if="screen === 'auth'" class="page">
        <div class="page-heading">
          <span class="eyebrow">WELCOME</span>
          <h1>Demo access</h1>
        </div>
        <div class="center-content">
          <LockKeyhole :size="46" class="teal" /><label
            class="field-label"
            for="access"
            >Access code</label
          ><input
            id="access"
            v-model="accessKey"
            type="password"
            autocomplete="off"
            @keydown.enter="start"
          />
        </div>
        <div class="page-actions">
          <button class="primary" :disabled="busy" @click="start">
            Start<ArrowRight :size="18" />
          </button>
        </div>
      </section>
      <section v-else-if="step >= 0" class="page question-page">
        <div class="question-progress" aria-label="Your three choices">
          <span
            v-for="n in 3"
            :key="n"
            :class="{ filled: n <= step + 1 }"
          ></span>
        </div>
        <div class="page-heading">
          <span class="eyebrow"
            >{{ String(step + 1).padStart(2, "0") }} / 03</span
          >
          <h1>
            {{
              step === 0
                ? "How many days?"
                : step === 1
                  ? "How much to invest?"
                  : "What matters most?"
            }}
          </h1>
        </div>
        <template v-if="screen === 'term'">
          <div class="term-grid" role="group" aria-label="Investment period">
            <button
              v-for="t in terms"
              :key="t.value"
              :aria-pressed="term === t.value"
              :aria-label="t.label"
              :class="{ chosen: term === t.value }"
              @click="term = t.value"
            >
              <strong
                >{{ t.short.split(" ")[0]
                }}<span class="term-unit">{{ t.unit }}</span></strong
              ><Check v-if="term === t.value" :size="13" />
            </button>
          </div>
          <div class="question-mascot" aria-hidden="true">
            <MascotFrame action="writing" :frame="0" label="" />
          </div>
        </template>
        <template v-else-if="screen === 'amount'">
          <div class="amount-editor">
            <div
              class="currency-switch"
              role="group"
              aria-label="Investment currency"
            >
              <button
                v-for="c in ['RLUSD', 'XRP']"
                :key="c"
                :aria-pressed="currency === c"
                :class="{ chosen: currency === c }"
                @click="currency = c"
              >
                {{ c }}
              </button>
            </div>
            <label for="investment" class="sr-only">Investment amount</label>
            <div class="amount-input">
              <input
                id="investment"
                v-model="amount"
                inputmode="decimal"
                autocomplete="off"
                :aria-invalid="!amountValid"
                @keydown.enter="advance"
              /><span>{{ currency }}</span>
            </div>
            <div class="amount-presets">
              <button
                v-for="n in ['100', '1,000', '10,000']"
                :key="n"
                @click="amount = n.replaceAll(',', '')"
              >
                {{ n }}
              </button>
            </div>
            <p v-if="!amountValid" class="validation">
              Enter 1–100,000 (up to 6 decimals).
            </p>
          </div>
          <div class="question-mascot" aria-hidden="true">
            <MascotFrame action="writing" :frame="1" label="" />
          </div>
        </template>
        <template v-else>
          <div class="protection-options">
            <button
              class="option-card"
              :disabled="busy"
              @click="chooseProtection(true)"
            >
              <span class="option-icon"
                ><ShieldCheck :size="64" :stroke-width="1.75" /></span
              ><strong>Principal protection</strong></button
            ><span class="choice-divider" aria-hidden="true"></span
            ><button
              class="option-card yield-option"
              :disabled="busy"
              @click="chooseProtection(false)"
            >
              <span class="option-icon"
                ><TrendingUp :size="64" :stroke-width="1.75" /></span
              ><strong>High Yield</strong>
            </button>
          </div>
          <div class="question-mascot" aria-hidden="true">
            <MascotFrame action="calling" :frame="0" label="" />
          </div>
        </template>
        <div v-if="step < 2" class="page-actions">
          <button
            class="primary"
            :disabled="busy || (step === 1 && !amountValid)"
            @click="advance"
          >
            Continue<ArrowRight :size="18" />
          </button>
        </div>
      </section>
      <section v-else-if="screen === 'speed'" class="page speed-page">
        <div class="page-heading">
          <span class="eyebrow">{{
            agentApproval ? "AGENT REQUEST" : "YOUR PLAN, YOUR PACE"
          }}</span>
          <h1>Find your next move.</h1>
          <div class="plan-tags">
            <span>{{ labelTerm(term) }}</span
            ><span>{{ money(amount) }} {{ currency }}</span
            ><span>{{
              protection ? "Principal protection" : "High Yield"
            }}</span>
          </div>
        </div>
        <div class="speed-options" role="group" aria-label="Discovery speed">
          <button
            class="speed-card"
            :class="{ chosen: tier === 'standard' }"
            :aria-pressed="tier === 'standard'"
            :disabled="agentApproval && tier !== 'standard'"
            @click="chooseTier('standard')"
          >
            <span class="speed-icon"><Gauge :size="30" /></span
            ><strong>Standard</strong><span class="speed-value">1x speed</span
            ><span class="speed-price"
              >{{ money(searchFee("standard"))
              }}<small>{{ feeUnit }}</small></span
            ><span class="speed-radio"
              ><Check v-if="tier === 'standard'" :size="13"
            /></span>
          </button>
          <button
            class="speed-card turbo-card"
            :class="{ chosen: tier === 'turbo' }"
            :aria-pressed="tier === 'turbo'"
            :disabled="agentApproval && tier !== 'turbo'"
            @click="chooseTier('turbo')"
          >
            <div
              v-if="tier === 'turbo'"
              :key="flameKey"
              class="flame-burst"
              aria-hidden="true"
            >
              <Flame v-for="n in 5" :key="n" :style="{ '--i': n }" :size="28" />
            </div>
            <span class="speed-icon"><Flame :size="30" /></span
            ><strong>Turbo</strong><span class="speed-value">2x speed</span
            ><span class="speed-price"
              >{{ money(searchFee("turbo")) }}<small>{{ feeUnit }}</small></span
            ><span class="speed-radio"
              ><Check v-if="tier === 'turbo'" :size="13"
            /></span>
          </button>
        </div>
        <div class="speed-footnote">
          <span><Layers :size="14" />Full collection, either way.</span
          ><span><Zap :size="14" />RFQ fee via x402</span
          ><small
            :style="{ visibility: tier === 'turbo' ? 'visible' : 'hidden' }"
            >2x applies to demo delivery.</small
          >
        </div>
        <div class="page-actions">
          <button
            class="primary"
            :class="{ 'turbo-cta': tier === 'turbo' }"
            :disabled="busy"
            @click="find"
          >
            {{ busy ? "Starting…" : "Find"
            }}<Flame v-if="tier === 'turbo'" :size="19" /><ArrowRight
              v-else
              :size="19"
            />
          </button>
        </div>
      </section>
      <section
        v-else-if="screen === 'searching' || screen === 'issuing'"
        class="page work-page"
      >
        <CharacterWorkflow
          v-if="!['failed', 'cancelled'].includes(workflowStatus)"
          :key="screen"
          :mode="screen === 'issuing' ? 'order' : 'search'"
          :status="workflowStatus"
          :phase="orderPhase"
          :turbo="isTurbo"
          :ready="screen === 'issuing' ? !!termSheet : quotes.length > 0"
          @complete="completeWorkflow"
        />
        <div v-else class="center-content">
          <RotateCcw :size="42" class="teal" />
          <h1>
            {{
              workflowStatus === "cancelled"
                ? "Search paused."
                : "Let’s try that again."
            }}
          </h1>
          <p class="muted compact-error">{{ workflowError }}</p>
        </div>
        <div class="page-actions">
          <button
            v-if="workflowStatus === 'payment_unknown'"
            class="primary"
            :disabled="busy"
            @click="resume"
          >
            Resume<RefreshCw :size="17" /></button
          ><button
            v-else-if="['failed', 'cancelled'].includes(workflowStatus)"
            class="primary"
            :disabled="busy || !canLeave"
            @click="home"
          >
            Back to start<Home :size="17" /></button
          ><button
            v-else-if="screen === 'searching'"
            class="text-button"
            :disabled="busy"
            @click="stop"
          >
            Cancel</button
          ><span v-else class="quiet-label">{{
            config.mpt_mode === "xrpl_testnet"
              ? "XRPL Testnet MPT"
              : "Demo MPT receipt"
          }}</span>
        </div>
      </section>
      <section
        v-else-if="screen === 'results' || screen === 'catalog'"
        class="page results-page"
        :class="{ 'sheet-grow': screen === 'results' }"
        data-testid="rfq-report"
      >
        <div class="page-heading">
          <span class="eyebrow">{{
            screen === "results" ? "PICKED FOR YOU" : "SIVAX COLLECTION"
          }}</span>
          <h1>
            {{ screen === "results" ? "Your top 3." : "Explore your matches." }}
          </h1>
          <div class="plan-tags">
            <span>{{ labelTerm(profile?.investment_term) }}</span
            ><span>{{ money(profile?.amount) }} {{ profile?.currency }}</span
            ><span>{{
              profile?.principal_protected
                ? "Principal protection"
                : "High Yield"
            }}</span>
          </div>
        </div>
        <div class="quote-list">
          <button
            v-for="(q, index) in visibleQuotes"
            :key="q.quote_id"
            class="quote-card"
            :class="{ best: screen === 'results' && index === 0 }"
            data-testid="quote-option"
            :disabled="busy"
            @click="openQuote(q)"
          >
            <div class="quote-top">
              <span class="product-type"
                >{{ q.terms.product_type.toUpperCase()
                }}<span
                  v-if="screen === 'results' && index === 0"
                  class="best-label"
                  ><Sparkles :size="10" />Best fit</span
                ></span
              ><ArrowUpRight :size="17" />
            </div>
            <div class="quote-main">
              <div>
                <h2>{{ name(q) }}</h2>
                <span class="quote-meta">{{
                  q.terms.principal_protected
                    ? "Principal protected"
                    : "Strike " + q.terms.strike_percent + "%"
                }}</span>
              </div>
              <div class="quote-rate">
                <strong>{{ percent(q.result.annualized_coupon) }}</strong
                ><span>p.a.</span>
              </div>
            </div>
          </button>
        </div>
        <div v-if="screen === 'catalog'" class="pagination">
          <button
            class="icon-button"
            aria-label="Previous products"
            :disabled="catalogPage === 0"
            @click="pageMove('catalog', -1)"
          >
            <ChevronLeft :size="18" /></button
          ><span
            >{{ catalogPage + 1 }} / {{ Math.ceil(quotes.length / 3) }}</span
          ><button
            class="icon-button"
            aria-label="Next products"
            :disabled="(catalogPage + 1) * 3 >= quotes.length"
            @click="pageMove('catalog', 1)"
          >
            <ChevronRight :size="18" />
          </button>
        </div>
        <div class="page-actions">
          <template v-if="screen === 'results'"
            ><button class="primary" :disabled="busy" @click="allProducts">
              View all<Layers :size="17" /></button
            ><button
              class="text-button"
              :disabled="busy || !canLeave"
              @click="home"
            >
              Back to start
            </button></template
          ><template v-else
            ><button
              class="primary"
              :disabled="busy || !canLeave"
              @click="startRound('another_pool')"
            >
              Try another pool<RefreshCw :size="17" /></button
            ><button
              class="text-button"
              :disabled="busy || !canLeave"
              @click="changeChoices"
            >
              Change my choices
            </button></template
          >
        </div>
      </section>
      <section
        v-else-if="screen === 'details' && selected"
        class="page detail-page"
      >
        <div class="page-heading">
          <span class="eyebrow"
            >SIVAX · {{ selected.terms.product_type.toUpperCase() }}</span
          >
          <h1>{{ name(selected) }}</h1>
        </div>
        <div class="yield-hero">
          <span>Annualized return</span
          ><strong
            >{{ percent(selected.result.annualized_coupon)
            }}<small>p.a.</small></strong
          >
        </div>
        <dl class="detail-grid">
          <div>
            <dt>Investment</dt>
            <dd>
              {{ money(selected.terms.amount) }} {{ selected.terms.currency }}
            </dd>
          </div>
          <div>
            <dt>Period</dt>
            <dd>{{ labelTerm(selected.terms.maturity) }}</dd>
          </div>
          <div>
            <dt>Starts</dt>
            <dd>{{ shortDate(selected.terms.start_date) }}</dd>
          </div>
          <div>
            <dt>Matures</dt>
            <dd>{{ shortDate(selected.terms.maturity_date) }}</dd>
          </div>
          <div>
            <dt>Principal</dt>
            <dd>
              {{ selected.terms.principal_protected ? "Protected" : "At risk" }}
            </dd>
          </div>
          <div>
            <dt>
              {{ selected.terms.product_type === "cma" ? "Product" : "Strike" }}
            </dt>
            <dd>
              {{
                selected.terms.product_type === "cma"
                  ? "CMA"
                  : selected.terms.strike_percent + "%"
              }}
            </dd>
          </div>
        </dl>
        <div class="page-actions">
          <div class="fee-line">
            <span><Zap :size="13" />Order fee · x402</span
            ><strong>{{ money(orderFee) }} {{ feeUnit }}</strong>
          </div>
          <button
            class="primary"
            :disabled="busy || !choiceReady || !!intent?.approved_at"
            @click="confirm"
          >
            {{ busy ? "Preparing…" : "Confirm" }}<Check :size="19" /></button
          ><button
            v-if="!busy && !choiceReady && !selectionExpired"
            class="text-button"
            @click="openQuote(selected)"
          >
            Refresh selection
          </button>
          <button
            v-else-if="selectionExpired"
            class="text-button"
            @click="home"
          >
            Back to start
          </button>
        </div>
      </section>
      <section
        v-else-if="screen === 'terms' && termSheet"
        class="page terms-page"
        :class="{ 'sheet-grow': revealTerms }"
        data-testid="mpt-receipt"
      >
        <div class="sheet-heading">
          <span class="issued-seal"><CircleCheck :size="22" /></span>
          <div>
            <span class="eyebrow">{{
              verified ? "MPT ISSUED" : "DEMO RECEIPT"
            }}</span>
            <h1>Your term sheet.</h1>
          </div>
        </div>
        <article class="term-paper">
          <div class="paper-top">
            <span class="wordmark">SIVAX</span
            ><span class="paper-code">{{
              sheet.product_type.toUpperCase()
            }}</span>
          </div>
          <h2>{{ name(termSheet) }}</h2>
          <div class="paper-return">
            <strong
              >{{ percent(sheetResult.annualized_coupon)
              }}<small>p.a.</small></strong
            ><span>Annualized return</span>
          </div>
          <dl class="paper-details">
            <div>
              <dt>Investment</dt>
              <dd>{{ money(sheet.amount) }} {{ sheet.currency }}</dd>
            </div>
            <div>
              <dt>Period</dt>
              <dd>{{ labelTerm(sheet.maturity) }}</dd>
            </div>
            <div>
              <dt>Starts / matures</dt>
              <dd>
                {{ shortDate(sheet.start_date) }} →
                {{ shortDate(sheet.maturity_date) }}
              </dd>
            </div>
            <div>
              <dt>Principal</dt>
              <dd>{{ sheet.principal_protected ? "Protected" : "At risk" }}</dd>
            </div>
          </dl>
          <div class="paper-verified">
            <ShieldCheck :size="13" />{{
              verified
                ? "Verified from XRPL · decrypted"
                : "Decrypted demo receipt"
            }}
          </div>
        </article>
        <div class="page-actions">
          <button class="primary" :disabled="busy || !canLeave" @click="home">
            Back to start<ArrowRight :size="18" /></button
          ><button class="text-button" @click="screen = 'termsDetail'">
            View full terms<ArrowUpRight :size="14" />
          </button>
        </div>
      </section>
      <section
        v-else-if="screen === 'termsDetail'"
        class="page terms-detail-page"
      >
        <div class="page-heading">
          <span class="eyebrow">DECRYPTED TERMS</span>
          <h1>The finer details.</h1>
        </div>
        <dl class="terms-rows">
          <div v-for="row in visibleSheetRows" :key="row.label">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </div>
        </dl>
        <div class="pagination">
          <button
            class="icon-button"
            aria-label="Previous terms"
            :disabled="termsPage === 0"
            @click="pageMove('terms', -1)"
          >
            <ChevronLeft :size="18" /></button
          ><span
            >{{ termsPage + 1 }} / {{ Math.ceil(sheetRows.length / 6) }}</span
          ><button
            class="icon-button"
            aria-label="Next terms"
            :disabled="(termsPage + 1) * 6 >= sheetRows.length"
            @click="pageMove('terms', 1)"
          >
            <ChevronRight :size="18" />
          </button>
        </div>
        <div class="page-actions">
          <button class="secondary" @click="downloadTerms">
            Download JSON<Download :size="17" />
          </button>
        </div>
      </section>
      <section v-else-if="screen === 'home'" class="page home-page">
        <div class="page-heading">
          <span class="eyebrow">WELCOME BACK</span>
          <h1>Your next move?</h1>
        </div>
        <div class="home-art">
          <img
            src="/mascot/calling-4.webp"
            alt="Your SIVAX Agent"
            width="150"
            height="150"
          />
        </div>
        <div class="home-options">
          <button class="option-card" @click="screen = 'holdings'">
            <span class="option-icon"><Wallet :size="23" /></span
            ><strong>My holdings</strong
            ><span class="count-badge">{{ holdings.length }}</span
            ><ChevronRight :size="17" /></button
          ><button
            class="option-card"
            :disabled="busy || !canLeave"
            @click="startRound('new')"
          >
            <span class="option-icon"><Sparkles :size="23" /></span
            ><strong>Start fresh</strong><ChevronRight :size="17" /></button
          ><button
            class="option-card"
            :disabled="!profiles.length"
            @click="screen = 'saved'"
          >
            <span class="option-icon"><RefreshCw :size="23" /></span
            ><strong>Refresh a saved plan</strong><ChevronRight :size="17" />
          </button>
        </div>
      </section>
      <section
        v-else-if="screen === 'holdings' || screen === 'saved'"
        class="page list-page"
      >
        <div class="page-heading">
          <span class="eyebrow">YOUR SIVAX DESK</span>
          <h1>
            {{
              screen === "holdings"
                ? "My holdings."
                : "Same plan. Fresh quotes."
            }}
          </h1>
        </div>
        <div v-if="screen === 'holdings'" class="saved-list">
          <button
            v-for="h in visibleHoldings"
            :key="h.order_id"
            class="holding-item list-card"
            :disabled="busy"
            @click="showTerms(h.order_id)"
          >
            <span class="product-type"
              >{{ h.status === "SIMULATED" ? "DEMO RECEIPT" : "TESTNET MPT"
              }}<FileText :size="16" /></span
            ><strong>{{ name(h) }}</strong
            ><span
              >{{ money(h.amount) }} {{ h.currency }}<ArrowUpRight :size="16"
            /></span>
          </button>
          <div v-if="!holdings.length" class="empty-state">
            <Wallet :size="36" />
            <h2>A fresh portfolio.</h2>
            <button class="text-button" @click="startRound('new')">
              Find your first match<ArrowRight :size="15" />
            </button>
          </div>
        </div>
        <div v-else class="saved-list">
          <button
            v-for="p in visibleProfiles"
            :key="p.id"
            class="list-card"
            :disabled="busy || !canLeave"
            @click="startRound('saved', p.id)"
          >
            <span class="product-type"
              >{{
                p.preferences.principal_protected
                  ? "PRINCIPAL PROTECTION"
                  : "HIGH YIELD"
              }}<RefreshCw :size="15" /></span
            ><strong
              >{{ money(p.preferences.amount) }}
              {{ p.preferences.currency }}</strong
            ><span
              >{{ labelTerm(p.preferences.investment_term)
              }}<ArrowUpRight :size="16"
            /></span>
          </button>
        </div>
        <div class="pagination">
          <button
            class="icon-button"
            aria-label="Previous saved items"
            :disabled="(screen === 'holdings' ? holdingPage : savedPage) === 0"
            @click="pageMove(screen === 'holdings' ? 'holding' : 'saved', -1)"
          >
            <ChevronLeft :size="18" /></button
          ><span
            >{{ (screen === "holdings" ? holdingPage : savedPage) + 1 }} /
            {{
              Math.max(
                1,
                Math.ceil(
                  (screen === "holdings" ? holdings : profiles).length / 3,
                ),
              )
            }}</span
          ><button
            class="icon-button"
            aria-label="Next saved items"
            :disabled="
              ((screen === 'holdings' ? holdingPage : savedPage) + 1) * 3 >=
              (screen === 'holdings' ? holdings : profiles).length
            "
            @click="pageMove(screen === 'holdings' ? 'holding' : 'saved', 1)"
          >
            <ChevronRight :size="18" />
          </button>
        </div>
        <div class="page-actions">
          <button class="secondary" @click="home">
            Back to start<Home :size="16" />
          </button>
        </div>
      </section>
      <section v-else-if="screen === 'menu'" class="page menu-page">
        <div class="page-heading">
          <span class="eyebrow">SIVAX AGENT</span>
          <h1>Your space.</h1>
        </div>
        <div class="home-options">
          <button
            class="option-card"
            :disabled="busy || !canLeave"
            @click="home"
          >
            <Home :size="22" /><strong>Back to start</strong
            ><ChevronRight :size="17" /></button
          ><button class="option-card" @click="screen = 'agent'">
            <Bot :size="22" /><strong>Connect an agent</strong
            ><ChevronRight :size="17" /></button
          ><button
            class="option-card"
            :disabled="busy || !canLeave"
            @click="screen = 'reset'"
          >
            <RotateCcw :size="22" /><strong>Reset demo</strong
            ><ChevronRight :size="17" />
          </button>
        </div>
        <div class="page-actions">
          <span class="quiet-label"
            >{{
              config.payment_mode === "xrpl_testnet"
                ? "XRPL Testnet ·"
                : "Mock mode ·"
            }}
            Simulated investments</span
          >
        </div>
      </section>
      <section v-else-if="screen === 'agent'" class="page">
        <div class="page-heading">
          <span class="eyebrow">A2A · 1.0</span>
          <h1>Meet your other agent.</h1>
        </div>
        <div class="agent-panel">
          <Bot :size="44" class="teal" />
          <p>Connect your AI agent.<br />You approve fees and orders here.</p>
          <template v-if="agentInfo"
            ><label class="field-label" for="credential">Agent credential</label
            ><input
              id="credential"
              :value="agentInfo.token"
              readonly
              type="password"
              autocomplete="off" /><button class="secondary" @click="copyToken">
              Copy credential<Copy :size="16" /></button
            ><a
              href="/.well-known/agent-card.json"
              target="_blank"
              rel="noreferrer"
              class="text-button"
              >Agent card<ArrowUpRight :size="14" /></a></template
          ><button
            v-else
            class="primary"
            :disabled="busy"
            @click="connectAgent"
          >
            Connect<ArrowRight :size="17" />
          </button>
        </div>
        <div class="page-actions">
          <button class="text-button" @click="back">Done</button>
        </div>
      </section>
      <section
        v-else-if="screen === 'reset' || screen === 'resetting'"
        class="page reset-page"
      >
        <div class="page-heading">
          <span class="eyebrow">FRESH START</span>
          <h1>
            {{
              screen === "reset"
                ? "Ready for a clean slate?"
                : "Making room for your next move."
            }}
          </h1>
        </div>
        <div class="center-content">
          <RotateCcw
            :size="44"
            class="teal"
            :class="{ spin: screen === 'resetting' }"
          />
          <p>
            {{
              screen === "reset"
                ? "Return receipt tokens, remove holder authorization and clear saved plans."
                : state?.reset?.progress?.step === "unauthorize"
                  ? "Removing authorization…"
                  : "Returning your receipts…"
            }}
          </p>
          <p v-if="state?.reset?.error" class="compact-error">
            {{ state.reset.error }}
          </p>
        </div>
        <div class="page-actions">
          <button
            v-if="screen === 'reset'"
            class="primary"
            :disabled="busy || !canLeave"
            @click="reset"
          >
            Return &amp; reset<RotateCcw :size="17" /></button
          ><button
            v-else-if="state?.reset?.state === 'needs_recovery'"
            class="primary"
            :disabled="busy"
            @click="reset"
          >
            Resume reset<RefreshCw :size="17" /></button
          ><button v-if="screen === 'reset'" class="text-button" @click="back">
            Keep my plans
          </button>
        </div>
      </section>
    </main>
  </div>
</template>
