import { describe, expect, it } from "vitest";
import { extractTicketmasterEventId, planForwardFamilies, taskFor } from "../src/forward-planner";

function makeEnv(channels: Array<Record<string, unknown>>, stateReads: { count: number }, events: Array<Record<string, unknown>> = []) {
  const pointer = { source: "control/watch_universe/v2/current.json", version: "v2" };
  const bucket = {
    get: async (key: string) => {
      if (key === "control/youtube/active_channels.json") {
        return { json: async () => ({ channels }) };
      }
      if (key === "control/watch_universe/current.json") {
        return { json: async () => pointer };
      }
      if (key === pointer.source) {
        return { json: async () => ({ version: "v2", events }) };
      }
      if (key.startsWith("control/youtube/state/")) stateReads.count++;
      return null;
    },
  } as unknown as R2Bucket;
  return {
    BACKUP_BUCKET: bucket,
    YOUTUBE_QUEUE: {} as Queue,
    STRUCTURED_API_QUEUE: {} as Queue,
    BROWSER_QUEUE: {} as Queue,
    MONID_QUEUE: {} as Queue,
    SOFTWARE_VERSION: "test",
    YOUTUBE_DAILY_QUOTA: "9000",
  };
}

describe("forward planner", () => {
  it("uses the active-channel snapshot without one R2 state read per channel", async () => {
    const stateReads = { count: 0 };
    const channels = Array.from({ length: 1200 }, (_, i) => ({
      artist_key: `artist_${i}`,
      youtube_channel_id: `channel_${i}`,
      hot: i < 250,
      status: i === 7 ? "QUARANTINED" : "ACTIVE",
    }));

    const plan = await planForwardFamilies(makeEnv(channels, stateReads), {
      now: new Date("2026-08-30T12:00:00Z"),
      youtube_hot_limit: 250,
      youtube_full_limit: 0,
    });

    expect(stateReads.count).toBe(0);
    expect(plan.families.find((x) => x.family === "YOUTUBE_CHANNEL")?.candidate).toBe(1199);
    expect(plan.tasks.YOUTUBE_CHANNEL).toHaveLength(249);
    expect(plan.tasks.YOUTUBE_CHANNEL.some((task) => task.event_key === "artist_7")).toBe(false);
  });

  it("does not enqueue ticket work on every one-minute wake-up", async () => {
    const stateReads = { count: 0 };
    const events = [
      { event_key: "tm_1", event_date: "2026-09-01", marketplace: "ticketmaster.com", marketplace_event_url: "https://www.ticketmaster.com/event/1", mapping_status: "EXACT_PROVIDER_ID" },
      { event_key: "web_1", event_date: "2026-09-01", marketplace: "vividseats.com", marketplace_event_url: "https://www.vividseats.com/event/1", mapping_status: "EXACT_PAGE_MATCH" },
    ];
    const minute = await planForwardFamilies(makeEnv([], stateReads, events), { now: new Date("2026-08-30T12:01:00Z") });
    expect(minute.tasks.TICKET_STRUCTURED).toHaveLength(0);
    expect(minute.tasks.TICKET_WEB).toHaveLength(0);

    const quarter = await planForwardFamilies(makeEnv([], stateReads, events), { now: new Date("2026-08-30T13:15:00Z") });
    expect(quarter.tasks.TICKET_STRUCTURED).toHaveLength(1);
    expect(quarter.tasks.TICKET_STRUCTURED[0].provider_event_id).toBe("1");
    expect(quarter.tasks.TICKET_WEB).toHaveLength(0);

    const webWindow = await planForwardFamilies(makeEnv([], stateReads, events), { now: new Date("2026-08-30T12:00:00Z") });
    expect(webWindow.tasks.TICKET_STRUCTURED).toHaveLength(1);
    // Monid FAST covers every accepted exact URL, including Ticketmaster pages.
    expect(webWindow.tasks.TICKET_WEB).toHaveLength(2);
    expect(webWindow.tasks.TICKET_WEB.map((t) => t.marketplace).sort()).toEqual([
      "ticketmaster.com",
      "vividseats.com",
    ]);
  });

  it("rotates Monid FAST across ticketmaster and ticketweb exact URLs", async () => {
    const events = [
      ...Array.from({ length: 30 }, (_, i) => ({
        event_key: `tm_${i}`,
        event_date: "2026-09-01",
        marketplace: "ticketmaster.com",
        marketplace_event_url: `https://www.ticketmaster.com/event/${i}`,
        mapping_status: "EXACT_PROVIDER_ID",
        provider_event_id: `id_${i}`,
      })),
      ...Array.from({ length: 10 }, (_, i) => ({
        event_key: `tw_${i}`,
        event_date: "2026-09-01",
        marketplace: "ticketweb.com",
        marketplace_event_url: `https://www.ticketweb.com/event/${i}`,
        mapping_status: "EXACT_PROVIDER_ID",
      })),
    ];
    const plan = await planForwardFamilies(makeEnv([], { count: 0 }, events), {
      now: new Date("2026-08-30T12:00:00Z"),
      web_limit: 25,
    });
    expect(plan.families.find((x) => x.family === "TICKET_WEB")?.candidate).toBe(40);
    expect(plan.tasks.TICKET_WEB).toHaveLength(25);
    expect(plan.tasks.TICKET_WEB.some((t) => t.marketplace === "ticketmaster.com")).toBe(true);
  });

  it("preserves native ids and filters structured rows without a usable native id", async () => {
    const now = new Date("2026-08-30T12:00:00Z");
    const native = { event_key: "native", marketplace: "Ticketmaster.com", provider_event_id: "native-42", canonical_url: "https://www.ticketmaster.com/not-an-event", mapping_status: "EXACT_PROVIDER_ID" };
    const encodedUrl = { event_key: "encoded", marketplace: "ticketmaster.com", marketplace_event_url: "https://www.ticketmaster.com/foo/event/abc%2F123?foo=bar#fragment", mapping_status: "EXACT_PAGE_MATCH" };
    const invalid = { event_key: "invalid", marketplace: "ticketmaster.com", marketplace_event_url: "https://www.ticketmaster.com/foo/venue/123", mapping_status: "EXACT_PAGE_MATCH" };
    const plan = await planForwardFamilies(makeEnv([], { count: 0 }, [native, encodedUrl, invalid]), { now });

    expect(extractTicketmasterEventId(native)).toBe("native-42");
    expect(extractTicketmasterEventId(encodedUrl)).toBe("abc/123");
    expect(plan.families.find((x) => x.family === "TICKET_STRUCTURED")?.candidate).toBe(2);
    expect(plan.tasks.TICKET_STRUCTURED.map((task) => task.event_key)).toEqual(["native", "encoded"]);
    expect(taskFor({ ...native, provider_event_id: "mapped-id" }, "TICKET_STRUCTURED", now).provider_event_id).toBe("mapped-id");
  });

  it("rotates a maximum of 25 structured tasks on each 15-minute window", async () => {
    const stateReads = { count: 0 };
    const events = Array.from({ length: 60 }, (_, i) => ({
      event_key: `tm_${i}`,
      event_date: "2026-09-01",
      marketplace: "ticketmaster.com",
      provider_event_id: `provider_${i}`,
      marketplace_event_url: `https://www.ticketmaster.com/event/provider_${i}`,
      mapping_status: "EXACT_PROVIDER_ID",
    }));
    const first = await planForwardFamilies(makeEnv([], stateReads, events), { now: new Date("2026-08-30T12:00:00Z") });
    const second = await planForwardFamilies(makeEnv([], stateReads, events), { now: new Date("2026-08-30T12:15:00Z") });
    const minute = await planForwardFamilies(makeEnv([], stateReads, events), { now: new Date("2026-08-30T12:01:00Z") });
    expect(first.tasks.TICKET_STRUCTURED).toHaveLength(25);
    expect(second.tasks.TICKET_STRUCTURED).toHaveLength(25);
    expect(new Set(first.tasks.TICKET_STRUCTURED.map((task) => task.event_key)).size).toBe(25);
    expect(first.tasks.TICKET_STRUCTURED.some((task) => second.tasks.TICKET_STRUCTURED.some((next) => next.event_key === task.event_key))).toBe(false);
    expect(minute.tasks.TICKET_STRUCTURED).toHaveLength(0);
  });

  it("keeps the deterministic YouTube schedule within the 9,000-unit daily quota", async () => {
    const stateReads = { count: 0 };
    const channels = Array.from({ length: 10_000 }, (_, i) => ({
      artist_key: `artist_${i}`,
      youtube_channel_id: `channel_${i}`,
      hot: i < 250,
      status: "ACTIVE",
    }));
    const env = makeEnv(channels, stateReads);
    let selectedToday = 0;
    for (let hour = 0; hour < 24; hour++) {
      const plan = await planForwardFamilies(env, { now: new Date(`2026-08-30T${String(hour).padStart(2, "0")}:00:00Z`) });
      selectedToday += plan.tasks.YOUTUBE_CHANNEL.length;
    }
    expect(selectedToday).toBe(9000);

    const dayOne = await planForwardFamilies(env, { now: new Date("2026-08-30T00:00:00Z") });
    const dayTwo = await planForwardFamilies(env, { now: new Date("2026-08-31T00:00:00Z") });
    const dayOneCold = new Set(dayOne.tasks.YOUTUBE_CHANNEL.slice(250).map((task) => task.target_url));
    const dayTwoCold = new Set(dayTwo.tasks.YOUTUBE_CHANNEL.slice(250).map((task) => task.target_url));
    expect(dayOneCold.size).toBe(3000);
    expect(dayTwoCold.size).toBe(3000);
    expect([...dayOneCold].some((channel) => dayTwoCold.has(channel))).toBe(false);
    expect(stateReads.count).toBe(0);
  });
});
