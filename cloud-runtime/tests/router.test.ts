import { describe, it, expect, vi, afterEach } from "vitest";
import { extractStructured, acquireUrl, RouterDeps } from "../src/acquisition";
import { planBootstrapWave, PlannerEnv } from "../src/planner";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Minimal R2 bucket serving a universe via the stable control pointer */
function mockBucket(events: any[]): R2Bucket {
  const pointerKey = "control/watch_universe/current.json";
  const universeKey = "canonical/2026-08-26T01-00-58Z/watch_universe_v1.json";
  const universe = JSON.stringify({ events });
  const get = async (key: string): Promise<R2Object | null> => {
    if (key === pointerKey) return { key, json: async () => ({ source: universeKey }) } as unknown as R2Object;
    if (key === universeKey) return { key, json: async () => JSON.parse(universe) } as unknown as R2Object;
    return null;
  };
  return { get, put: async () => ({} as R2Object), delete: async () => {}, list: async () => ({ objects: [], truncated: false }) } as unknown as R2Bucket;
}

function mockQueue() {
  return { send: async () => {}, batch: async () => {} } as unknown as Queue;
}

function makeEnv(events: any[]): PlannerEnv {
  return { BACKUP_BUCKET: mockBucket(events), FAST_QUEUE: mockQueue(), SOFTWARE_VERSION: "test" };
}

function modernEvent(key: string, date: string, url: string, status = "EXACT_PAGE_MATCH") {
  return { event_key: key, event_date: date, marketplace_event_url: url, mapping_status: status, artist_name: "A", venue_name: "V", city: "C" };
}

/** Stub the global fetch so the direct rail can succeed/fail deterministically. */
function stubFetch(status: number, body: string, url = "https://x.test/any") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const resp: Partial<Response> = {
        ok: status >= 200 && status < 300,
        status,
        url,
        headers: new Headers({ "content-type": "text/html" }),
        text: async () => body,
      };
      return resp as Response;
    })
  );
}

/** Monid fetch stub that records calls. */
function monidDeps(calls: any[]): { deps: RouterDeps; calls: any[] } {
  const deps: RouterDeps = {
    browser: null,
    monidApiKey: "test-key",
    monidFetchPage: async (_k, url) => {
      calls.push(url);
      return { status: "FETCHED", html: "<html><body>monid</body></html>", provider: "context.dev", cost_usd: 0.0009, latency_ms: 10 };
    },
  };
  return { deps, calls };
}

describe("extractStructured (neutral price semantics)", () => {
  it("extracts offer price from JSON-LD with PUBLIC_PAGE_JSON_LD_OFFER basis and inventory UNKNOWN", () => {
    const html = `<script type="application/ld+json">{"@type":"MusicEvent","name":"Show","offers":{"price":"49.5","priceCurrency":"USD","availability":"https://schema.org/InStock"}}</script>`;
    const r = extractStructured(html, "ticketmaster.com");
    expect(r.observed_offer_min_price).toBe(49.5);
    expect(r.price_basis).toBe("PUBLIC_PAGE_JSON_LD_OFFER");
    expect(r.inventory_basis).toBe("UNKNOWN");
    expect(r.availability_state).toContain("InStock");
  });

  it("does NOT invent a resale basis from a generic JSON-LD offer", () => {
    const html = `<script type="application/ld+json">{"@type":"MusicEvent","name":"Show","offers":{"price":"80","priceCurrency":"USD"}}</script>`;
    const r = extractStructured(html, "stubhub.com");
    // Neutral: we record the offer min price and basis, but inventory basis stays UNKNOWN.
    expect(r.observed_offer_min_price).toBe(80);
    expect(r.price_basis).toBe("PUBLIC_PAGE_JSON_LD_OFFER");
    expect(r.inventory_basis).toBe("UNKNOWN");
  });

  it("returns UNKNOWN/empty when no structured data present", () => {
    const r = extractStructured("<html><p>no data</p></html>", "ticketmaster.com");
    expect(r.observed_offer_min_price).toBeNull();
    expect(r.price_basis).toBe("NONE");
    expect(r.inventory_basis).toBe("UNKNOWN");
  });
});

describe("acquireUrl rail hierarchy (cheapest first)", () => {
  it("uses the FREE direct rail when it succeeds, no Monid call", async () => {
    const html = `<script type="application/ld+json">{"@type":"Event","name":"Show","offers":{"price":"25","priceCurrency":"USD"}}</script>`;
    stubFetch(200, html);
    const { deps, calls } = monidDeps([]);
    const res = await acquireUrl(deps, "evt_1", "ticketmaster.com", "https://x.test/evt", "v_test");
    expect(res.acquisition_rail).toBe("RAIL_0_DIRECT_HTTP");
    expect(res.acquisition_provider).toBe("direct");
    expect(res.observed_offer_min_price).toBe(25);
    expect(calls.length).toBe(0); // Monid NOT called
  });

  it("falls back to Monid (RAIL_4) when the direct rail fails", async () => {
    stubFetch(403, "<html>blocked</html>");
    const { deps, calls } = monidDeps([]);
    const res = await acquireUrl(deps, "evt_1", "ticketmaster.com", "https://x.test/evt", "v_test");
    expect(res.acquisition_rail).toBe("RAIL_4_MONID");
    expect(res.acquisition_provider).toBe("monid");
    expect(calls.length).toBe(1);
  });

  it("reports a failed result when every rail is unsupported/unavailable", async () => {
    stubFetch(500, "err");
    const deps: RouterDeps = {
      browser: null,
      monidApiKey: null, // no Monid configured
      monidFetchPage: async () => ({ status: "FETCH_FAILED", html: "", cost_usd: 0, latency_ms: 0 }),
    };
    const res = await acquireUrl(deps, "evt_1", "ticketmaster.com", "https://x.test/evt", "v_test");
    expect(res.acquisition_rail).toBe("RAIL_UNSUPPORTED");
    expect(res.error_category).toBe("BLOCKED");
  });
});

describe("planBootstrapWave (initial collection vs lifecycle refresh)", () => {
  it("queues never-observed accepted pairs immediately, honoring max_cost_usd", async () => {
    const events = Array.from({ length: 10 }, (_, i) => modernEvent(`evt_${i}`, "2099-01-01", `https://www.ticketmaster.com/${i}`));
    const env = makeEnv(events);
    const result = await planBootstrapWave({ ...env, governorBudget: async () => ({ daily_spend: 0, reserved: 0, daily_budget: 0.25 }) }, {
      never_observed_only: true,
      lastObserved: async () => false,
      max_cost_usd: 0.009, // 10 × $0.0009 exactly
      max_tasks: 10,
    });
    expect(result.due_pairs).toBe(10);
    expect(result.queued).toBe(10);
    expect(result.selected.length).toBe(10);
  });

  it("trims selection when max_cost_usd cannot cover all eligible pairs", async () => {
    const events = Array.from({ length: 20 }, (_, i) => modernEvent(`evt_${i}`, "2099-01-01", `https://www.ticketmaster.com/${i}`));
    const env = makeEnv(events);
    // max_cost_usd = $0.0045 → at most 5 tasks (5 × $0.0009)
    const result = await planBootstrapWave({ ...env, governorBudget: async () => ({ daily_spend: 0, reserved: 0, daily_budget: 0.25 }) }, {
      never_observed_only: true,
      lastObserved: async () => false,
      max_cost_usd: 0.0045,
      max_tasks: 20,
    });
    expect(result.queued).toBeLessThanOrEqual(5);
    expect(result.queued).toBe(5);
  });

  it("dry_run plans but does not enqueue", async () => {
    const events = [modernEvent("evt_1", "2099-01-01", "https://www.ticketmaster.com/1")];
    const env = makeEnv(events);
    const result = await planBootstrapWave({ ...env, governorBudget: async () => ({ daily_spend: 0, reserved: 0, daily_budget: 0.25 }) }, {
      never_observed_only: true,
      lastObserved: async () => false,
      dry_run: true,
    });
    expect(result.dry_run).toBe(true);
    expect(result.queued).toBe(0);
    expect(result.selected.length).toBe(1);
  });

  it("excludes already-observed pairs when never_observed_only is true", async () => {
    const events = [modernEvent("evt_done", "2099-01-01", "https://www.ticketmaster.com/done"), modernEvent("evt_new", "2099-01-01", "https://www.ticketmaster.com/new")];
    const env = makeEnv(events);
    const result = await planBootstrapWave({ ...env, governorBudget: async () => ({ daily_spend: 0, reserved: 0, daily_budget: 0.25 }) }, {
      never_observed_only: true,
      lastObserved: async (_e, m) => m === "_done" ? true : false,
      max_tasks: 10,
    });
    // lastObserved mock keys off url via marketplace; default returns false → both eligible.
    // We simulate the observed one by checking event_key instead in the callback below:
    const strict = await planBootstrapWave({ ...env, governorBudget: async () => ({ daily_spend: 0, reserved: 0, daily_budget: 0.25 }) }, {
      never_observed_only: true,
      lastObserved: async (ek) => ek === "evt_done",
      max_tasks: 10,
    });
    expect(strict.due_pairs).toBe(1);
    expect(strict.queued).toBe(1);
    expect(strict.selected[0].event_key).toBe("evt_new");
    expect(result.queued).toBe(2); // sanity: default callback excludes nothing
  });

  it("respects a marketplace filter", async () => {
    const events = [
      modernEvent("eg1", "2099-01-01", "https://seatgeek.com/1"),
      modernEvent("tm1", "2099-01-01", "https://www.ticketmaster.com/1"),
    ];
    const env = makeEnv(events);
    const result = await planBootstrapWave({ ...env, governorBudget: async () => ({ daily_spend: 0, reserved: 0, daily_budget: 0.25 }) }, {
      never_observed_only: true,
      lastObserved: async () => false,
      marketplace: "seatgeek.com",
      max_tasks: 10,
    });
    expect(result.queued).toBe(1);
    expect(result.selected[0].event_key).toBe("eg1");
  });
});