import { describe, it, expect, vi, afterEach } from "vitest";
import {
  normalizeName,
  parseEventDate,
  matchCandidate,
  parseCandidate,
  selectBestMapping,
  fetchSitemapUrls,
  marketplaceFromHost,
  EventIdentity,
} from "../src/mapping";

afterEach(() => {
  vi.unstubAllGlobals();
});

const identity: EventIdentity = {
  event_key: "evt_1",
  artist_name: "The National",
  event_date: "2026-11-06",
  venue_name: "Madison Square Garden",
  city: "New York",
};

describe("normalizeName", () => {
  it("lowercases, strips punctuation, collapses whitespace", () => {
    expect(normalizeName("  The National — LIVE! ")).toBe("national");
    expect(normalizeName("Madison Square Garden")).toBe("madison square garden");
  });
});

describe("parseEventDate", () => {
  it("parses ISO, US and textual dates", () => {
    expect(parseEventDate("2026-11-06")).toBe("2026-11-06");
    expect(parseEventDate("11/06/2026")).toBe("2026-11-06");
    expect(parseEventDate("Nov 6, 2026")).toBe("2026-11-06");
    expect(parseEventDate("garbage")).toBeNull();
  });
});

describe("marketplaceFromHost", () => {
  it("resolves marketplace hosts from URLs", () => {
    expect(marketplaceFromHost("https://seatgeek.com/events/x")).toBe("seatgeek.com");
    expect(marketplaceFromHost("https://www.vividseats.com/e/1")).toBe("vividseats.com");
    expect(marketplaceFromHost("https://www.ticketmaster.com/event/1")).toBe("ticketmaster.com");
    expect(marketplaceFromHost("https://gametime.co/events/1")).toBe("gametime.com");
  });
});

describe("matchCandidate (deterministic identity match)", () => {
  it("EXACT_PAGE_MATCH requires artist + date + venue + city", () => {
    const { status, confidence } = matchCandidate(identity, {
      url: "https://seatgeek.com/the-national-11-06-2026-msg",
      title: "The National - Fri, Nov 6, 2026 at Madison Square Garden, New York",
      marketplace: "seatgeek.com",
      artist: "The National",
      event_date: "2026-11-06",
      venue: "Madison Square Garden",
      city: "New York",
    });
    expect(status).toBe("EXACT_PAGE_MATCH");
    expect(confidence).toBe(1.0);
  });

  it("HIGH_CONFIDENCE when venue or city missing but artist+date present", () => {
    const { status } = matchCandidate(identity, {
      url: "https://x.test/1",
      title: "The National - Nov 6, 2026 at Madison Square Garden",
      marketplace: "ticketmaster.com",
      artist: "The National",
      event_date: "2026-11-06",
      venue: "Madison Square Garden",
    });
    expect(status).toBe("HIGH_CONFIDENCE");
  });

  it("AMBIGUOUS when only artist + date match (artist-only matching forbidden)", () => {
    const { status } = matchCandidate(identity, {
      url: "https://x.test/2",
      title: "The National - Nov 6, 2026",
      marketplace: "ticketmaster.com",
      artist: "The National",
      event_date: "2026-11-06",
    });
    expect(status).toBe("AMBIGUOUS");
  });

  it("rejects artist-only matches", () => {
    const { status } = matchCandidate(identity, {
      url: "https://x.test/3",
      title: "The National",
      marketplace: "ticketmaster.com",
      artist: "The National",
    });
    expect(status).toBe("NOT_FOUND");
  });

  it("NOT_FOUND when date mismatches", () => {
    const { status } = matchCandidate(identity, {
      url: "https://x.test/4",
      title: "The National - Nov 7, 2026 at Madison Square Garden, New York",
      marketplace: "ticketmaster.com",
      artist: "The National",
      event_date: "2026-11-07",
      venue: "Madison Square Garden",
      city: "New York",
    });
    expect(status).toBe("NOT_FOUND");
  });
});

describe("parseCandidate", () => {
  it("parses artist/date/venue/city from a structured title", () => {
    const cand = parseCandidate("https://seatgeek.com/x", "The National - Fri, Nov 6, 2026 at Madison Square Garden, New York");
    expect(cand.artist).toBe("The National");
    expect(cand.event_date).toBe("2026-11-06");
    expect(cand.venue).toContain("Madison Square Garden");
    expect(cand.city).toContain("New York");
  });
});

describe("selectBestMapping", () => {
  it("picks the exact match over weaker candidates", () => {
    const candidates = [
      { url: "https://x.test/weak", title: "The National - Nov 6, 2026 at Some Other Venue", marketplace: "ticketmaster.com", artist: "The National", event_date: "2026-11-06", venue: "Some Other Venue" },
      { url: "https://x.test/exact", title: "The National - Fri, Nov 6, 2026 at Madison Square Garden, New York", marketplace: "seatgeek.com", artist: "The National", event_date: "2026-11-06", venue: "Madison Square Garden", city: "New York" },
    ];
    const { record, status, best } = selectBestMapping(identity, candidates);
    expect(status).toBe("EXACT_PAGE_MATCH");
    expect(record?.marketplace_event_url).toBe("https://x.test/exact");
    expect(best?.url).toBe("https://x.test/exact");
  });

  it("reports NOT_FOUND when nothing matches", () => {
    const { record, status } = selectBestMapping(identity, [
      { url: "https://x.test/nope", title: "Random Other Band - Nov 6, 2026", marketplace: "ticketmaster.com", artist: "Other Band", event_date: "2026-11-06" },
    ]);
    expect(status).toBe("NOT_FOUND");
    expect(record).toBeNull();
  });
});

describe("fetchSitemapUrls", () => {
  it("extracts loc URLs from a urlset", async () => {
    const xml = '<?xml version="1.0"?><urlset><url><loc>https://seatgeek.com/event/1</loc></url><url><loc>https://seatgeek.com/event/2</loc></url></urlset>';
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200, text: async () => xml,
    }) as unknown as Response));
    const urls = await fetchSitemapUrls("https://seatgeek.com/sitemap.xml");
    expect(urls).toContain("https://seatgeek.com/event/1");
    expect(urls).toContain("https://seatgeek.com/event/2");
  });

  it("returns [] on fetch failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 403 }) as unknown as Response));
    const urls = await fetchSitemapUrls("https://seatgeek.com/sitemap.xml");
    expect(urls).toEqual([]);
  });
});