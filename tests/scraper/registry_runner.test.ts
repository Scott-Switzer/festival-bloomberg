import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  assertRegistryConsistency,
  CANONICAL_REGISTRY,
  EDITION_REGISTRY,
  FESTIVAL_REGISTRY,
  FESTIVAL_SOURCES,
  getSourceById,
  resolveSourcesByIds,
  SOURCE_REGISTRY,
  UnknownSourceError,
  VENUE_REGISTRY,
} from "../../src/scraper/registry";
import { Fetcher, FetchError, LineupParser } from "../../src/scraper/runner";

describe("canonical registry consistency", () => {
  it("validates and keeps derived registries aligned on year 2026", () => {
    assertRegistryConsistency();

    assert.equal(CANONICAL_REGISTRY.festivals.length, FESTIVAL_REGISTRY.length);
    assert.equal(SOURCE_REGISTRY.length, FESTIVAL_REGISTRY.length);
    assert.equal(FESTIVAL_SOURCES.length, FESTIVAL_REGISTRY.length);

    for (const edition of EDITION_REGISTRY) {
      assert.equal(edition.year, 2026);
    }
    for (const source of FESTIVAL_SOURCES) {
      assert.equal(source.year, 2026);
    }

    for (const fest of FESTIVAL_REGISTRY) {
      const source = getSourceById(fest.slug);
      assert.ok(source, `missing festival source for slug ${fest.slug}`);
      assert.equal(source.name, fest.name);
      assert.ok(fest.primaryDomain);
      assert.equal(
        SOURCE_REGISTRY.some((s) => s.domain === fest.primaryDomain),
        true,
      );
      const host = new URL(source.url).host;
      assert.equal(host, fest.primaryDomain);
    }

    for (const edition of EDITION_REGISTRY) {
      for (const venueId of edition.venueIds) {
        assert.ok(VENUE_REGISTRY.some((v) => v.id === venueId));
      }
    }
  });

  it("rejects unknown --sources IDs without synthetic fallbacks", () => {
    assert.throws(
      () => resolveSourcesByIds(["not-a-festival"]),
      (err: unknown) => {
        assert.ok(err instanceof UnknownSourceError);
        assert.match(err.message, /Unknown source ID\(s\): not-a-festival/);
        assert.deepEqual(err.unknownIds, ["not-a-festival"]);
        assert.ok(err.registeredIds.includes("coachella"));
        return true;
      },
    );

    const resolved = resolveSourcesByIds(["coachella"]);
    assert.equal(resolved.length, 1);
    assert.equal(resolved[0].id, "coachella");
    assert.equal(resolved[0].year, 2026);
  });
});

describe("runner fetch + parser dispatch", () => {
  it("fails cleanly on real fetch failures without mock HTML", async () => {
    const fetcher = new Fetcher({
      fetchImpl: async () => {
        throw new Error("network down");
      },
    });

    await assert.rejects(
      () => fetcher.fetch("https://www.coachella.com"),
      (err: unknown) => {
        assert.ok(err instanceof FetchError);
        assert.match(err.message, /Failed to fetch/);
        assert.match(err.message, /network down|playwright_unavailable/);
        assert.equal(err.url, "https://www.coachella.com");
        return true;
      },
    );
  });

  it("fails on non-OK HTTP without generating synthetic lineup HTML", async () => {
    const fetcher = new Fetcher({
      fetchImpl: async () =>
        new Response("blocked", { status: 403, statusText: "Forbidden" }),
    });

    await assert.rejects(
      () => fetcher.fetch("https://www.bonnaroo.com"),
      (err: unknown) => {
        assert.ok(err instanceof FetchError);
        assert.match(err.message, /http_403/);
        assert.ok(!err.message.toLowerCase().includes("mock"));
        return true;
      },
    );
  });

  it("dispatches unimplemented specialized parsers to verified generic", () => {
    const parser = new LineupParser();
    assert.equal(parser.resolveParser("generic"), "generic");
    assert.equal(parser.resolveParser("coachella"), "generic");
    assert.equal(parser.resolveParser("bonnaroo"), "generic");
    assert.equal(parser.resolveParser("lollapalooza"), "generic");
    assert.equal(parser.resolveParser("glastonbury"), "generic");

    const source = getSourceById("coachella");
    assert.ok(source);
    assert.equal(source.parser, "coachella");

    const html = `
      <div class="lineup">
        <div class="artist"><a href="/a/radiohead">Radiohead</a></div>
        <div class="artist"><a href="/a/beyonce">Beyoncé</a></div>
      </div>
    `;
    const lineup = parser.parse(html, source);
    assert.equal(lineup.parserUsed, "generic");
    assert.equal(lineup.year, 2026);
    assert.deepEqual(
      lineup.artists.map((a) => a.name),
      ["Radiohead", "Beyoncé"],
    );
  });
});
