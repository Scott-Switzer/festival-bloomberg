import { describe, it, expect, vi, afterEach } from "vitest";
import {
  promoteProviderId,
  resolveMarketplaceForProvider,
  toEventIdentity,
  runMappingFactory,
  MappingFactoryEnv,
  ProviderIdentityEvent,
} from "../src/mapping-factory-v2";
import { queryCcUrlIndex, cdxTimestampToIso, latestCrawlId } from "../src/common-crawl";
import { marketplaceFromHost, normalizeName } from "../src/mapping";

afterEach(() => {
  vi.unstubAllGlobals();
});

function providerEvent(key: string, overrides: Partial<ProviderIdentityEvent> = {}): ProviderIdentityEvent {
  return {
    event_key: key,
    artist_name: "Weatherday",
    event_date: "2026-11-06",
    venue_name: "Bottom Lounge",
    city: "Chicago",
    provider_event_id: "rZ7HnEZ1AfOyZK",
    canonical_url: "https://www.ticketmaster.com/weatherday-chicago-illinois-11-06-2026/event/040064CADD7D1593",
    ...overrides,
  };
}

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

function makeEnv(events: any[], browser: any = null): MappingFactoryEnv {
  return { BACKUP_BUCKET: mockBucket(events), BROWSER: browser, SOFTWARE_VERSION: "test" };
}

describe("provider-ID promotion (Source 1)", () => {
  it("promotes a canonical event with provider_event_id + canonical_url to EXACT_PROVIDER_ID", () => {
    const rec = promoteProviderId(providerEvent("evt_1"), "2026-08-26T00:00:00Z");
    expect(rec).not.toBeNull();
    expect(rec!.mapping_status).toBe("EXACT_PROVIDER_ID");
    expect(rec!.mapping_method).toBe("provider_id_promotion");
    expect(rec!.marketplace).toBe("ticketmaster.com");
    expect(rec!.marketplace_event_id).toBe("rZ7HnEZ1AfOyZK");
    expect(rec!.confidence).toBe(1.0);
  });

  it("returns null when provider ID or canonical URL is missing (no promotion)", () => {
    expect(promoteProviderId(providerEvent("evt_2", { provider_event_id: undefined }))).toBeNull();
    expect(promoteProviderId(providerEvent("evt_3", { canonical_url: undefined }))).toBeNull();
  });

  it("promotes ticketweb canonical URLs to ticketweb.com marketplace", () => {
    const rec = promoteProviderId(
      providerEvent("evt_4", { canonical_url: "https://www.ticketweb.com/event/weatherday-bottom-lounge-tickets/14839013" })
    );
    expect(rec!.marketplace).toBe("ticketweb.com");
    expect(rec!.marketplace_event_id).toBe("rZ7HnEZ1AfOyZK");
  });

  it("keeps ticketmaster.com marketplace for white-label hosts (never invents a marketplace)", () => {
    const rec = promoteProviderId(
      providerEvent("evt_5", { canonical_url: "https://www.universe.com/events/weatherday-123" })
    );
    expect(rec!.marketplace).toBe("ticketmaster.com");
    expect(rec!.marketplace_event_url).toBe("https://www.universe.com/events/weatherday-123");
    expect(rec!.mapping_status).toBe("EXACT_PROVIDER_ID");
  });

  it("resolveMarketplaceForProvider maps known hosts and falls back to the provider", () => {
    expect(resolveMarketplaceForProvider(providerEvent("evt_6", { canonical_url: "https://www.ticketweb.com/event/x/1" }))).toBe("ticketweb.com");
    expect(resolveMarketplaceForProvider(providerEvent("evt_7", { canonical_url: "https://www.axs.com/events/x" }))).toBe("axs.com");
    expect(resolveMarketplaceForProvider(providerEvent("evt_8", { canonical_url: "https://www.universe.com/events/x" }))).toBe("ticketmaster.com");
    expect(resolveMarketplaceForProvider(providerEvent("evt_9", { canonical_url: "https://www.toyotacenter.com/event/x" }))).toBe("ticketmaster.com");
  });
});

describe("identity contract (artist + date + venue + city)", () => {
  it("toEventIdentity requires all four fields", () => {
    const full = toEventIdentity(providerEvent("evt_1"));
    expect(full).not.toBeNull();
    expect(toEventIdentity(providerEvent("evt_1", { city: "" }))).toBeNull();
    expect(toEventIdentity(providerEvent("evt_1", { venue_name: "" }))).toBeNull();
    expect(toEventIdentity(providerEvent("evt_1", { event_date: "" }))).toBeNull();
  });
});

describe("runMappingFactory", () => {
  it("promotes all provider-native events with zero scraper cost (dry run)", async () => {
    const events = Array.from({ length: 5 }, (_, i) => providerEvent(`evt_${i}`, {
      provider_event_id: `pid_${i}`,
      canonical_url: `https://www.ticketmaster.com/evt-${i}/event/0400${i}`,
    }));
    const report = await runMappingFactory(makeEnv(events), {
      max_events: 5,
      dry_run: true,
      include_calendars: false,
      include_common_crawl: false,
    });
    expect(report.provider_id_eligible).toBe(5);
    expect(report.provider_id_accepted).toBe(5);
    expect(report.accepted_mappings.length).toBe(5);
    expect(report.by_status["EXACT_PROVIDER_ID"]).toBe(5);
    expect(report.by_source["PROVIDER_ID"]).toBe(5);
  });

  it("skips events without provider identity (no forced coverage)", async () => {
    const events = [
      providerEvent("evt_1"),
      providerEvent("evt_2", { provider_event_id: undefined, canonical_url: undefined }),
    ];
    const report = await runMappingFactory(makeEnv(events), {
      max_events: 2,
      dry_run: true,
      include_calendars: false,
      include_common_crawl: false,
    });
    expect(report.provider_id_accepted).toBe(1);
    expect(report.accepted_mappings.length).toBe(1);
  });
});

describe("Common Crawl URL index (bounded queries)", () => {
  it("parses CDX timestamps into ISO 8601 with correct PIT semantics", () => {
    expect(cdxTimestampToIso("20240801120000")).toBe("2024-08-01T12:00:00Z");
    expect(cdxTimestampToIso("20240801")).toBe("2024-08-01T00:00:00Z");
    expect(cdxTimestampToIso("bad")).toBeNull();
  });

  it("returns captures from a mocked index response", async () => {
    const line = JSON.stringify({
      url: "https://www.ticketmaster.com/weatherday-chicago-illinois-11-06-2026/event/040064CADD7D1593",
      timestamp: "20240801120000",
      statuscode: "200",
      digest: "ABC123",
      length: "12345",
      mime: "text/html",
      filename: "crawl-data/CC-MAIN-2024-30/segments/000/0.warc.gz",
    });
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => `${line}\n`,
    })));
    const { captures, error } = await queryCcUrlIndex({ urlPattern: "https://www.ticketmaster.com/weatherday*", limit: 5 });
    expect(error).toBeUndefined();
    expect(captures.length).toBe(1);
    expect(captures[0].source_as_of).toBe("2024-08-01T12:00:00Z");
    expect(captures[0].warc_locator).toContain("data.commoncrawl.org");
  });

  it("handles 404 (no captures) gracefully without error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404 })));
    const { captures, error } = await queryCcUrlIndex({ urlPattern: "https://www.ticketmaster.com/nonexistent*" });
    expect(captures.length).toBe(0);
    expect(error).toBeUndefined();
  });

  it("bounds the result limit (no bulk queries)", async () => {
    const lines = Array.from({ length: 10 }, (_, i) =>
      JSON.stringify({ url: `https://x.com/${i}`, timestamp: `2024080112000${i}`, statuscode: "200" })
    ).join("\n");
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => lines })));
    const { captures } = await queryCcUrlIndex({ urlPattern: "https://x.com/*", limit: 3 });
    expect(captures.length).toBe(3);
  });

  it("latestCrawlId falls back to the default when collinfo is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
    const id = await latestCrawlId();
    expect(id).toBe("CC-MAIN-2026-30");
  });
});

describe("helpers", () => {
  it("marketplaceFromHost detects ticketmaster/ticketweb/axs", () => {
    expect(marketplaceFromHost("https://www.ticketmaster.com/event/x")).toBe("ticketmaster.com");
    expect(marketplaceFromHost("https://www.ticketweb.com/event/x")).toBe("ticketweb.com");
    expect(marketplaceFromHost("https://www.axs.com/events/x")).toBe("axs.com");
    expect(marketplaceFromHost("https://seatgeek.com/events/x")).toBe("seatgeek.com");
  });

  it("normalizeName strips stop-words deterministically", () => {
    expect(normalizeName("The Weatherday Live")).toBe("weatherday");
  });
});
