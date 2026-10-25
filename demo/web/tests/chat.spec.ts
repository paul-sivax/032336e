import { test, expect, type Page } from "@playwright/test";

async function fits(page: Page) {
  const problems = await page.evaluate(() => {
    const result: string[] = [];
    if (Math.abs(scrollY) > 1) result.push("document scrolled");
    if (
      Math.abs(document.querySelector(".topbar")!.getBoundingClientRect().top) >
      1
    )
      result.push("header is not fixed");
    if (
      document.documentElement.scrollHeight > innerHeight + 1 ||
      document.documentElement.scrollWidth > innerWidth + 1
    )
      result.push("document overflow");
    for (const selector of [".viewport", ".page"]) {
      const e = document.querySelector(selector) as HTMLElement;
      if (
        e &&
        (e.scrollHeight > e.clientHeight + 2 ||
          e.scrollWidth > e.clientWidth + 2)
      )
        result.push(
          selector +
            " overflow " +
            e.scrollHeight +
            "/" +
            e.clientHeight +
            " wide " +
            e.scrollWidth +
            "/" +
            e.clientWidth,
        );
    }
    const top = document
      .querySelector(".topbar")!
      .getBoundingClientRect().bottom;
    for (const e of document.querySelectorAll(
      "main button,main input,main h1,main .quote-card,main .term-paper,main .terms-rows",
    )) {
      const b = e.getBoundingClientRect();
      if (!b.width || !b.height) continue;
      if (
        b.top < top - 1 ||
        b.bottom > innerHeight + 1 ||
        b.left < 0 ||
        b.right > innerWidth + 1
      )
        result.push(
          (e.textContent || e.getAttribute("aria-label") || e.tagName).slice(
            0,
            50,
          ) + " outside viewport",
        );
    }
    return result;
  });
  expect(problems).toEqual([]);
}
async function enter(page: Page, info?: any) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "Your Personal Investment Agent on XRPL",
    }),
  ).toBeVisible();
  await fits(page);
  if (info) await screenshot(page, info, "00-landing");
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "How many days?" }),
  ).toBeVisible();
}
async function choices(
  page: Page,
  protectedProduct = false,
  amount = "100",
  tier = "standard",
  info?: any,
) {
  await enter(page, info);
  await expect(
    page.getByRole("button", { name: "1 business day", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await fits(page);
  if (info) await screenshot(page, info, "00a-term");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "How much to invest?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "RLUSD", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    await page.locator(".currency-switch button").allTextContents(),
  ).toEqual(["RLUSD", "XRP"]);
  await page.getByLabel("Investment amount", { exact: true }).fill(amount);
  await fits(page);
  if (info) await screenshot(page, info, "00b-amount");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "What matters most?" }),
  ).toBeVisible();
  await fits(page);
  if (info) await screenshot(page, info, "00c-protection");
  await page
    .getByRole("button", {
      name: protectedProduct ? "Principal protection" : "High Yield",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("button", { name: "Find", exact: true }),
  ).toBeVisible();
  if (tier === "turbo")
    await page.getByRole("button", { name: /Turbo 2x speed/ }).click();
  await fits(page);
}
async function report(page: Page) {
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(page.getByTestId("rfq-report")).toBeVisible();
  await page.waitForTimeout(750);
  await fits(page);
}
async function screenshot(page: Page, info: any, file: string) {
  await page.screenshot({ path: info.outputPath(file + ".png"), scale: "css" });
}

test("mobile journey: one-page choices, max batch, direct Confirm, stamping, decrypted terms, repeat and reset", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await choices(page, false, "100", "standard", info);
  await screenshot(page, info, "01-standard");
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "writing",
  );
  await expect(
    page.getByRole("progressbar", { name: "Discovery progress" }),
  ).toBeVisible();
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "calling",
  );
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "reporting",
  );
  await expect(page.getByTestId("rfq-report")).toBeVisible();
  await page.waitForTimeout(750);
  await fits(page);
  await screenshot(page, info, "02-top-three");
  await expect(page.getByTestId("quote-option")).toHaveCount(3);
  await expect(page.getByText(/Income Note/i)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Try another pool" }),
  ).toHaveCount(0);
  const state = await (await page.request.get("/api/state")).json();
  const search = state.jobs.find(
    (j: any) => j.request.operation === "find_fcn_quotes",
  );
  expect(search.output.quotes).toHaveLength(30);
  expect(search.receipts).toHaveLength(1);
  expect(search.receipts[0].merchant_fee_drops).toBe("30000");
  expect(search.quotes.every((q: any) => q.seller_id === "SIVAX")).toBe(true);
  await page.getByTestId("quote-option").nth(1).click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeEnabled();
  await fits(page);
  await screenshot(page, info, "03-confirm");
  await expect(page.getByRole("button", { name: /Review/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.locator(".workflow")).toHaveAttribute("data-mode", "order");
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "stamping",
  );
  await screenshot(page, info, "04-mpt-stamping");
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "presenting",
  );
  await expect(
    page.getByRole("img", {
      name: "SIVAX Agent holding up your completed term sheet",
    }),
  ).toBeVisible();
  await expect(page.getByTestId("mpt-receipt")).toHaveCount(0);
  await fits(page);
  await screenshot(page, info, "04a-document-handoff");
  await expect(page.getByTestId("mpt-receipt")).toBeVisible();
  await page.waitForTimeout(750);
  await fits(page);
  await screenshot(page, info, "05-terms");
  const filled = await (await page.request.get("/api/state")).json();
  const order = filled.jobs.find(
    (j: any) => j.request.operation === "execute_demo_order",
  );
  expect(filled.intents[0].quote.quote_id).toBe(
    search.output.quotes[1].quote_id,
  );
  expect(order.output.principal_transferred).toBe(false);
  expect(order.output.mpt.status).toBe("SIMULATED");
  await page.getByRole("button", { name: "View full terms" }).click();
  for (let i = 0; i < 6; i++) {
    await fits(page);
    const next = page.getByRole("button", { name: "Next terms" });
    if (await next.isDisabled()) break;
    await next.click();
  }
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download JSON" }).click();
  expect((await downloadPromise).suggestedFilename()).toBe(
    "sivax-term-sheet.json",
  );
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page
    .getByRole("button", { name: "Back to start", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Your next move?" }),
  ).toBeVisible();
  await fits(page);
  await page.getByRole("button", { name: /My holdings/ }).click();
  await expect(page.locator(".holding-item")).toHaveCount(1);
  await fits(page);
  await page.locator(".holding-item").click();
  await expect(page.getByTestId("mpt-receipt")).toBeVisible();
  await page
    .getByRole("button", { name: "Back to start", exact: true })
    .click();
  await page.getByRole("button", { name: "Refresh a saved plan" }).click();
  await fits(page);
  await page.locator(".list-card").first().click();
  await expect(
    page.getByRole("button", { name: "Find", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open menu" }).click();
  await fits(page);
  await page.getByRole("button", { name: "Reset demo", exact: true }).click();
  await fits(page);
  await page.getByRole("button", { name: "Return & reset" }).click();
  await expect(
    page.getByRole("heading", { name: "How many days?" }),
  ).toBeVisible();
  const reset = await (await page.request.get("/api/state")).json();
  expect(reset.holdings).toHaveLength(0);
  expect(reset.profiles).toHaveLength(0);
  expect(errors).toEqual([]);
});

test("Turbo doubles the x402 fee, keeps full collection, and selects CMA for protection", async ({
  page,
}, info) => {
  await choices(page, true, "500.125", "turbo");
  await expect(page.locator(".flame-burst")).toBeVisible();
  await screenshot(page, info, "06-turbo");
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(page.locator(".turbo-workflow")).toBeVisible();
  await screenshot(page, info, "07-turbo-writing");
  await expect(page.getByTestId("rfq-report")).toBeVisible();
  await page.waitForTimeout(750);
  await fits(page);
  const state = await (await page.request.get("/api/state")).json();
  const search = state.jobs[0];
  expect(search.request.service_tier).toBe("turbo");
  expect(search.output.quotes).toHaveLength(12);
  expect(search.receipts[0].merchant_fee_drops).toBe("24000");
  expect(search.receipts[0].unit_fee_drops).toBe("2000");
  expect(search.receipts).toHaveLength(1);
  expect(
    search.output.quotes.every(
      (q: any) =>
        q.terms.product_type === "cma" && q.terms.underlyings.length === 0,
    ),
  ).toBe(true);
  await page.getByTestId("quote-option").first().click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByTestId("mpt-receipt")).toBeVisible();
  await page.waitForTimeout(750);
  await fits(page);
  await expect(page.getByTestId("mpt-receipt")).toContainText("500.125 RLUSD");
});

test("full collection is paginated and unlocks another pool", async ({
  page,
}, info) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await choices(page);
  await report(page);
  await page.getByRole("button", { name: "View all", exact: true }).click();
  await expect(page.getByTestId("quote-option")).toHaveCount(3);
  await fits(page);
  await screenshot(page, info, "08-collection");
  const before = await page.getByTestId("quote-option").allTextContents();
  await page.getByRole("button", { name: "Next products" }).click();
  expect(await page.getByTestId("quote-option").allTextContents()).not.toEqual(
    before,
  );
  await fits(page);
  const first = (await (await page.request.get("/api/state")).json()).jobs[0];
  await page.getByRole("button", { name: "Try another pool" }).click();
  await report(page);
  const state = await (await page.request.get("/api/state")).json();
  const second = state.jobs.find((j: any) => j.id !== first.id);
  expect(second.request.preferences.pool_round).toBe(1);
  expect(
    second.request.preferences.asset_pool.some((a: string) =>
      first.request.preferences.asset_pool.includes(a),
    ),
  ).toBe(false);
  expect(second.output.quotes).toHaveLength(30);
  expect(state.reserved_drops).toBe("30000");
});

test("lost grant response retries one authorization; lost Confirm response recovers one order", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await choices(page);
  const grants: any[] = [];
  await page.route("**/api/grants", async (route) => {
    grants.push(route.request().postDataJSON());
    const r = await route.fetch();
    if (grants.length === 1) await route.abort("failed");
    else await route.fulfill({ response: r });
  });
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await report(page);
  expect(grants[0].authorization_id).toBe(grants[1].authorization_id);
  await page.getByTestId("quote-option").first().click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeEnabled();
  await page.route("**/api/intents/*/confirm", async (route) => {
    await route.fetch();
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByTestId("mpt-receipt")).toBeVisible({ timeout: 12000 });
  const state = await (await page.request.get("/api/state")).json();
  expect(
    state.jobs.filter((j: any) => j.request.operation === "execute_demo_order"),
  ).toHaveLength(1);
  expect(state.reserved_drops).toBe("35000");
});

test("small screens retain all controls and show a whole term sheet without scrolling", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await choices(page);
  await screenshot(page, info, "09-small-speed");
  await report(page);
  await screenshot(page, info, "10-small-results");
  await page.getByRole("button", { name: "View all", exact: true }).click();
  await fits(page);
  await page.getByTestId("quote-option").first().click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeEnabled();
  await fits(page);
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByTestId("mpt-receipt")).toBeVisible();
  await fits(page);
  await screenshot(page, info, "11-small-terms");
  await page.getByRole("button", { name: "View full terms" }).click();
  for (let i = 0; i < 6; i++) {
    await fits(page);
    const next = page.getByRole("button", { name: "Next terms" });
    if (await next.isDisabled()) break;
    await next.click();
  }
});

test("agent request gets a human fee gate and one Confirm without re-entering questions", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await enter(page);
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("button", { name: "Connect an agent" }).click();
  await fits(page);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByLabel("Agent credential")).toBeVisible();
  const token = await page.getByLabel("Agent credential").inputValue();
  async function agent(body: any) {
    const r = await page.request.post("/a2a", {
      headers: { Authorization: "Bearer " + token, "A2A-Version": "1.0" },
      data: {
        jsonrpc: "2.0",
        id: crypto.randomUUID(),
        method: "SendMessage",
        params: {
          message: {
            messageId: crypto.randomUUID(),
            role: "ROLE_USER",
            parts: [{ data: body, mediaType: "application/json" }],
          },
        },
      },
    });
    const d = await r.json();
    expect(r.ok()).toBe(true);
    expect(d.error).toBeUndefined();
    return d;
  }
  await agent({
    operation: "find_fcn_quotes",
    operation_id: crypto.randomUUID(),
    service_tier: "turbo",
    preferences: {
      investment_term: "1bd",
      amount: "250",
      currency: "RLUSD",
      principal_protected: false,
    },
  });
  await expect(
    page.getByRole("button", { name: "Find", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Standard 1x speed/ }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: /Turbo 2x speed/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await fits(page);
  await report(page);
  const searched = (
    await (await page.request.get("/api/state")).json()
  ).jobs.find((j: any) => j.request.operation === "find_fcn_quotes");
  expect(searched.caller).not.toBe("browser");
  expect(searched.receipts[0].merchant_fee_drops).toBe("60000");
  await agent({
    operation: "prepare_demo_order",
    operation_id: crypto.randomUUID(),
    search_id: searched.id,
    amount: "250",
    currency: "RLUSD",
  });
  let intent: any;
  await expect
    .poll(async () => {
      const s = await (await page.request.get("/api/state")).json();
      intent = s.intents[0];
      return !!intent;
    })
    .toBe(true);
  await agent({
    operation: "execute_demo_order",
    operation_id: crypto.randomUUID(),
    intent_id: intent.id,
  });
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeEnabled();
  await fits(page);
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByTestId("mpt-receipt")).toBeVisible();
  const state = await (await page.request.get("/api/state")).json();
  expect(
    state.jobs.filter((j: any) => j.request.operation === "execute_demo_order"),
  ).toHaveLength(1);
});

test("amount input remains usable with a keyboard-sized viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await enter(page);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.setViewportSize({ width: 375, height: 380 });
  await page.getByLabel("Investment amount", { exact: true }).fill("1000");
  await fits(page);
  await page.setViewportSize({ width: 375, height: 667 });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "What matters most?" }),
  ).toBeVisible();
});
