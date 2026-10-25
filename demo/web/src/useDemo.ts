import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { terms } from "./copy";

type Data = Record<string, any>;
export function useDemo() {
  const state = ref<Data | null>(null),
    config = ref<Data>({}),
    screen = ref("landing");
  const busy = ref(false),
    loading = ref(true),
    error = ref(""),
    connected = ref(false);
  const accessKey = ref("local-demo-only"),
    term = ref("1bd"),
    amount = ref("100"),
    currency = ref("RLUSD");
  const protection = ref<boolean | null>(null),
    tier = ref("standard"),
    pool = ref<string[]>([]),
    poolRound = ref(0);
  const selectedId = ref(""),
    catalogPage = ref(0),
    holdingPage = ref(0),
    savedPage = ref(0),
    termsPage = ref(0);
  const termSheet = ref<Data | null>(null),
    agentInfo = ref<Data | null>(null),
    returnTo = ref("landing");
  const flameKey = ref(0),
    revealTerms = ref(false),
    now = ref(Date.now() / 1000);
  let stream: EventSource | undefined,
    poll: ReturnType<typeof setInterval>,
    timer: ReturnType<typeof setInterval>;
  let refreshPromise: Promise<void> | undefined,
    seenSearch = "",
    seenCycle = "",
    seenApproval = "",
    sheetLoading = "",
    initialized = false;
  const id = () => crypto.randomUUID();
  const workspace = computed(() => state.value?.workspace || {});
  const jobs = computed<Data[]>(() =>
    (state.value?.jobs || []).filter(
      (j: Data) =>
        j.request.schema_version === "sivax.agent-request.v4" &&
        (j.cycle_id || "initial") === workspace.value.cycle_id,
    ),
  );
  const search = computed(
    () =>
      jobs.value
        .filter((j) => j.request.operation === "find_fcn_quotes")
        .sort((a, b) => b.created_at - a.created_at)[0],
  );
  const quotes = computed<Data[]>(() => search.value?.output?.quotes || []);
  const selected = computed(
    () =>
      quotes.value.find((q) => q.quote_id === selectedId.value) ||
      search.value?.output?.reservation?.quote,
  );
  const intent = computed(
    () =>
      (state.value?.intents || []).find(
        (i: Data) => i.id === search.value?.active_intent_id,
      ) ||
      (state.value?.intents || [])
        .filter((i: Data) => i.search_id === search.value?.id)
        .sort((a: Data, b: Data) => b.expires_at - a.expires_at)[0],
  );
  const execution = computed(
    () =>
      jobs.value
        .filter(
          (j) =>
            j.request.operation === "execute_demo_order" &&
            j.request.intent_id === intent.value?.id,
        )
        .sort((a, b) => b.created_at - a.created_at)[0],
  );
  const canLeave = computed(
    () =>
      !(state.value?.jobs || []).some((j: Data) =>
        ["queued", "running", "payment_unknown"].includes(j.state),
      ) && !workspace.value.reset_id,
  );
  const profile = computed(() => search.value?.request.preferences);
  const holdings = computed<Data[]>(() => state.value?.holdings || []);
  const profiles = computed<Data[]>(() =>
    [...(state.value?.profiles || [])].sort((a, b) => b.saved_at - a.saved_at),
  );
  const visibleQuotes = computed(() =>
    quotes.value.slice(
      screen.value === "catalog" ? catalogPage.value * 3 : 0,
      screen.value === "catalog" ? catalogPage.value * 3 + 3 : 3,
    ),
  );
  const visibleHoldings = computed(() =>
    holdings.value.slice(holdingPage.value * 3, holdingPage.value * 3 + 3),
  );
  const visibleProfiles = computed(() =>
    profiles.value.slice(savedPage.value * 3, savedPage.value * 3 + 3),
  );
  const amountValid = computed(
    () =>
      /^(0|[1-9]\d{0,5})(\.\d{1,6})?$/.test(amount.value) &&
      Number(amount.value) >= 1 &&
      Number(amount.value) <= 100000,
  );
  const fullCount = computed(() => {
    const p = search.value?.state === "input_required" ? profile.value : null;
    if (p ? p.principal_protected : protection.value) return 12;
    const count = (p?.asset_pool || pool.value).length || 6;
    return count * (count - 1);
  });
  const feeUnit = computed(() =>
    config.value.payment_mode === "xrpl_testnet" ? "XRP" : "credits",
  );
  const searchFee = (choice: string) =>
    (fullCount.value *
      Number(config.value.rfq_fee_drops || 1000) *
      (choice === "turbo" ? 2 : 1)) /
    1e6;
  const orderFee = computed(
    () => Number(config.value.order_fee_drops || 5000) / 1e6,
  );
  const percent = (value: any) => (Number(value) * 100).toFixed(2) + "%";
  const money = (value: any) =>
    Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 6 });
  const labelTerm = (value: string) =>
    terms.find((t) => t.value === value)?.short || value;
  const name = (q: Data) =>
    (q?.display_name || "")
      .replace(/\s+Income Note$/, "")
      .replace(/^SIVAX CMA\s*·\s*/, "CMA · ");
  const shortDate = (value?: string) =>
    value
      ? new Intl.DateTimeFormat("en-US", {
          month: "short",
          day: "numeric",
          timeZone: "UTC",
        }).format(new Date(value + "T12:00:00Z"))
      : "—";
  const choiceReady = computed(
    () =>
      intent.value?.quote.quote_id === selected.value?.quote_id &&
      intent.value?.expires_at > now.value,
  );
  const selectionExpired = computed(
    () => selected.value?.expires_at <= now.value && !choiceReady.value,
  );
  const orderPhase = computed(
    () => state.value?.order_progress?.phase || "preparing",
  );
  const isTurbo = computed(
    () => search.value?.request.service_tier === "turbo",
  );
  const workflowError = computed(() =>
    screen.value === "issuing" ? execution.value?.error : search.value?.error,
  );
  const workflowStatus = computed(() =>
    screen.value === "issuing"
      ? execution.value?.state || "running"
      : search.value?.state || "running",
  );
  const sheetRows = computed(() => {
    if (!termSheet.value) return [];
    const labels: Data = {
      underlyings: "Underlying assets",
      product_type: "Product",
      principal_protected: "Principal protection",
      amount: "Investment amount",
      currency: "Currency",
      strike_percent: "Strike %",
      knock_in: "Knock-in",
      autocall: "Autocall",
      monthly_coupon: "Monthly coupon",
      maturity: "Investment period",
      valuation_date: "Valuation date",
      pricing_at: "Pricing time",
      start_date: "Start date",
      maturity_date: "Maturity date",
      business_day_calendar: "Calendar",
      market_timezone: "Market timezone",
      day_count: "Day count",
      days: "Accrual days",
      coupon_schedule: "Payment schedule",
      payoff_version: "Payoff model",
      settlement_currency: "Settlement currency",
      cma_variant: "CMA option",
      annualized_coupon: "Annualized return",
      coupon_per_period: "Period return",
      coupon_amount: "Return amount",
      terms_hash: "Terms hash",
    };
    return Object.entries({
      ...termSheet.value.terms,
      ...termSheet.value.result,
      terms_hash: termSheet.value.terms_hash,
    })
      .filter(([, v]) => v !== null)
      .map(([key, value]) => ({
        label: labels[key] || key,
        value: Array.isArray(value)
          ? value.join(" + ") || "Cash"
          : typeof value === "boolean"
            ? value
              ? "Yes"
              : "No"
            : String(value),
      }));
  });
  const visibleSheetRows = computed(() =>
    sheetRows.value.slice(termsPage.value * 6, termsPage.value * 6 + 6),
  );

  async function api(path: string, body?: Data) {
    const response = await fetch("/api" + path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(state.value ? { "X-CSRF-Token": state.value.session.csrf } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Please check your choices and try again.",
      );
    return data;
  }
  function fillPreferences(p?: Data) {
    term.value = p?.investment_term || "1bd";
    amount.value = p?.amount || "100";
    currency.value = p?.currency || "RLUSD";
    protection.value = p?.principal_protected ?? null;
    pool.value = p?.asset_pool || [];
    poolRound.value = p?.pool_round || 0;
  }
  async function refresh() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      const data = await api("/state");
      state.value = data;
      config.value = data.config;
      if (seenCycle !== data.workspace.cycle_id) {
        seenCycle = data.workspace.cycle_id;
        fillPreferences(data.workspace.draft_preferences);
        selectedId.value = "";
        catalogPage.value =
          holdingPage.value =
          savedPage.value =
          termsPage.value =
            0;
        if (
          initialized &&
          screen.value === "resetting" &&
          !data.workspace.reset_id
        )
          screen.value = "term";
      }
      if (data.workspace.reset_id) screen.value = "resetting";
      const j = search.value;
      if (j && seenSearch !== j.id) {
        seenSearch = j.id;
        catalogPage.value = 0;
        selectedId.value = "";
        if (
          initialized &&
          j.state === "input_required" &&
          j.request.preferences
        ) {
          fillPreferences(j.request.preferences);
          tier.value = j.request.service_tier || "standard";
          screen.value = "speed";
        } else if (
          initialized &&
          ["queued", "running", "payment_unknown"].includes(j.state)
        )
          screen.value = "searching";
      }
      const ex = execution.value;
      if (
        initialized &&
        ex?.state === "input_required" &&
        ex.required_action?.type === "human_order_approval" &&
        seenApproval !== ex.id
      ) {
        seenApproval = ex.id;
        selectedId.value = intent.value.quote.quote_id;
        screen.value = "details";
      }
      if (
        ex &&
        (["queued", "running", "payment_unknown"].includes(ex.state) ||
          (ex.state === "completed" &&
            screen.value === "details" &&
            intent.value?.approved_at)) &&
        !["landing", "menu", "agent"].includes(screen.value)
      )
        screen.value = "issuing";
      if (
        ex?.state === "completed" &&
        screen.value === "issuing" &&
        !termSheet.value &&
        sheetLoading !== ex.output.order_id
      ) {
        sheetLoading = ex.output.order_id;
        try {
          termSheet.value = await api(
            "/orders/" + ex.output.order_id + "/termsheet",
          );
        } catch (e) {
          error.value = (e as Error).message;
        } finally {
          sheetLoading = "";
        }
      }
    })().finally(() => {
      refreshPromise = undefined;
    });
    return refreshPromise;
  }
  function pending(kind: string, payload: Data) {
    const key = "sivax-mobile-" + state.value!.session.owner + "-" + kind;
    const fingerprint = JSON.stringify(payload),
      old = sessionStorage.getItem(key);
    if (old) {
      const saved = JSON.parse(old);
      if (saved.fingerprint === fingerprint) return saved.id as string;
    }
    const op = id();
    sessionStorage.setItem(key, JSON.stringify({ id: op, fingerprint }));
    return op;
  }
  async function action(fn: () => Promise<void>) {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    try {
      await fn();
      if (state.value) await refresh();
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  }
  function connect() {
    stream?.close();
    stream = new EventSource("/api/events");
    stream.onopen = () => {
      connected.value = true;
    };
    stream.onerror = () => {
      connected.value = false;
    };
    stream.onmessage = () => {
      refresh().catch(() => {});
    };
  }
  async function start() {
    await action(async () => {
      if (!state.value) {
        const session = await api("/session", { access_key: accessKey.value });
        state.value = { session, jobs: [], intents: [] };
        connect();
      }
      await refresh();
      if (workspace.value.reset_id) {
        screen.value = "resetting";
        return;
      }
      if (
        execution.value &&
        ["queued", "running", "payment_unknown"].includes(execution.value.state)
      ) {
        screen.value = "issuing";
        return;
      }
      if (
        search.value &&
        ["queued", "running", "payment_unknown"].includes(search.value.state)
      ) {
        screen.value = "searching";
        return;
      }
      if (execution.value?.state === "input_required") {
        selectedId.value = intent.value.quote.quote_id;
        screen.value = "details";
        return;
      }
      if (search.value?.state === "input_required" && profile.value) {
        fillPreferences(profile.value);
        tier.value = search.value.request.service_tier || "standard";
        screen.value = "speed";
        return;
      }
      if (holdings.value.length || profiles.value.length) {
        await api("/workspace/home", {});
        screen.value = "home";
      } else {
        fillPreferences(workspace.value.draft_preferences);
        screen.value = workspace.value.draft_preferences ? "speed" : "term";
      }
    });
  }
  function advance() {
    if (screen.value === "term") screen.value = "amount";
    else if (screen.value === "amount" && amountValid.value)
      screen.value = "protection";
  }
  function chooseProtection(value: boolean) {
    protection.value = value;
    tier.value = "standard";
    screen.value = "speed";
  }
  function chooseTier(value: string) {
    tier.value = value;
    if (value === "turbo") flameKey.value++;
  }
  async function find() {
    await action(async () => {
      if (
        search.value?.state === "input_required" &&
        search.value.caller !== "browser" &&
        profile.value
      ) {
        await api("/grants", {
          max_rfqs: fullCount.value,
          caller: search.value.caller,
          job_id: search.value.id,
          service_tier: search.value.request.service_tier || "standard",
          authorization_id: "mobile-grant-" + search.value.id,
        });
        screen.value = "searching";
        return;
      }
      if (!amountValid.value || protection.value === null)
        throw new Error("Complete your three choices.");
      const p = {
        investment_term: term.value,
        amount: amount.value,
        currency: currency.value,
        principal_protected: protection.value,
        ...(pool.value.length ? { asset_pool: pool.value } : {}),
        pool_round: poolRound.value,
      };
      const count = fullCount.value,
        op = pending("search", {
          cycle: workspace.value.cycle_id,
          preferences: p,
          service_tier: tier.value,
        });
      const grant = await api("/grants", {
        max_rfqs: count,
        authorization_id: op,
        service_tier: tier.value,
      });
      await api("/operations", {
        operation: "find_fcn_quotes",
        operation_id: op,
        preferences: p,
        spend_authorization_id: grant.id,
        service_tier: tier.value,
      });
      termSheet.value = null;
      screen.value = "searching";
    });
  }
  async function waitJob(key: string) {
    for (let i = 0; i < 80; i++) {
      await refresh();
      const j = jobs.value.find((j) => j.id === key);
      if (j && !["queued", "running"].includes(j.state)) {
        if (j.state !== "completed")
          throw new Error(j.error || "This product could not be selected.");
        return j;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("Your selection is still preparing. Tap again to resume.");
  }
  async function openQuote(q: Data) {
    selectedId.value = q.quote_id;
    screen.value = "details";
    if (
      intent.value?.quote.quote_id === q.quote_id &&
      intent.value.expires_at > now.value
    )
      return;
    await action(async () => {
      const payload = {
        quote_id: q.quote_id,
        amount: q.terms.amount,
        currency: q.terms.currency,
      };
      const r = await api("/searches/" + search.value.id + "/selection", {
        ...payload,
        operation_id: pending("selection", {
          search_id: search.value.id,
          ...payload,
          renewal:
            intent.value?.quote.quote_id === q.quote_id &&
            intent.value.expires_at <= now.value
              ? intent.value.id
              : undefined,
        }),
      });
      await waitJob(r.id);
    });
  }
  async function confirm() {
    if (!choiceReady.value) {
      if (selected.value) await openQuote(selected.value);
      return;
    }
    await action(async () => {
      termSheet.value = null;
      revealTerms.value = true;
      const r = await api("/intents/" + intent.value.id + "/confirm", {
        intent_hash: intent.value.intent_hash,
      });
      if (r.state === "failed") throw new Error(r.error);
      screen.value = "issuing";
    });
  }
  async function allProducts() {
    await action(async () => {
      await api("/operations/" + search.value.id + "/review-all", {});
      catalogPage.value = 0;
      screen.value = "catalog";
    });
  }
  async function startRound(mode: string, profileId?: string) {
    await action(async () => {
      const w = await api("/workspace/start", {
        action_id: pending("round", {
          cycle: workspace.value.cycle_id,
          mode,
          profileId,
        }),
        mode,
        profile_id: profileId,
        search_id: mode === "another_pool" ? search.value.id : undefined,
      });
      await refresh();
      fillPreferences(w.draft_preferences);
      tier.value = "standard";
      termSheet.value = null;
      screen.value = w.draft_preferences ? "speed" : "term";
    });
  }
  async function changeChoices() {
    const p = { ...profile.value };
    await startRound("new");
    if (!error.value) {
      fillPreferences(p);
      screen.value = "term";
    }
  }
  async function home() {
    await action(async () => {
      await api("/workspace/home", {});
      screen.value = "home";
    });
  }
  async function showTerms(orderId: string) {
    await action(async () => {
      termSheet.value = await api("/orders/" + orderId + "/termsheet");
      revealTerms.value = false;
      termsPage.value = 0;
      screen.value = "terms";
    });
  }
  async function reset() {
    await action(async () => {
      await api("/reset", {
        reset_id:
          state.value?.reset?.id ||
          pending("reset", { cycle: workspace.value.cycle_id }),
      });
      screen.value = "resetting";
    });
  }
  async function resume() {
    await action(async () => {
      const j = screen.value === "issuing" ? execution.value : search.value;
      if (j) await api("/operations/" + j.id + "/resume", {});
    });
  }
  async function stop() {
    await action(async () => {
      await api("/operations/" + search.value.id + "/cancel", {});
    });
  }
  function menu() {
    returnTo.value = screen.value;
    screen.value = "menu";
  }
  function back() {
    error.value = "";
    const dest: Data = {
      amount: "term",
      protection: "amount",
      speed: "protection",
      catalog: "results",
      details: search.value?.reviewed_all ? "catalog" : "results",
      termsDetail: "terms",
      holdings: "home",
      saved: "home",
      agent: "menu",
      reset: "menu",
      auth: "landing",
    };
    if (screen.value === "menu") {
      screen.value = returnTo.value;
      return;
    }
    if (screen.value === "term") {
      screen.value =
        profiles.value.length || holdings.value.length ? "home" : "landing";
      return;
    }
    if (dest[screen.value]) {
      screen.value = dest[screen.value];
      return;
    }
    if (["results", "terms", "home"].includes(screen.value)) home();
  }
  async function connectAgent() {
    await action(async () => {
      agentInfo.value = await api("/agent-credentials", {});
    });
  }
  async function copyToken() {
    try {
      await navigator.clipboard.writeText(agentInfo.value!.token);
    } catch {
      error.value = "Could not copy the credential.";
    }
  }
  function downloadTerms() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(termSheet.value, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "sivax-term-sheet.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function completeWorkflow() {
    if (screen.value === "searching") screen.value = "results";
    else if (screen.value === "issuing" && termSheet.value) {
      termsPage.value = 0;
      screen.value = "terms";
    }
  }
  watch(screen, () => {
    error.value = "";
  });
  onMounted(async () => {
    try {
      config.value = await api("/config");
      try {
        await refresh();
        connect();
      } catch {
        state.value = null;
      }
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      loading.value = false;
      initialized = true;
    }
    poll = setInterval(() => {
      if (state.value) refresh().catch(() => {});
    }, 700);
    timer = setInterval(() => {
      now.value = Date.now() / 1000;
    }, 1000);
  });
  onUnmounted(() => {
    stream?.close();
    clearInterval(poll);
    clearInterval(timer);
  });
  return {
    state,
    config,
    screen,
    busy,
    loading,
    error,
    connected,
    accessKey,
    term,
    amount,
    currency,
    protection,
    tier,
    pool,
    poolRound,
    selectedId,
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
    fullCount,
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
  };
}
