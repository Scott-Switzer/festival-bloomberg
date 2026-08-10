import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, it } from "node:test";
import { createDuckDbWarehouse } from "../../src/scraper/db";
import { computeEditionAnalyticalMetrics } from "../../src/scraper/intelligence_metrics";
import { IntelligenceMetricsStore } from "../../src/scraper/intelligence_store";
import {
  WikimediaPageviewsClient,
  INTELLIGENCE_METRIC_VERSION,
} from "../../src/scraper/wikimedia_pageviews";

const fixture = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../tests/fixtures/intelligence/pageviews_radiohead.json"),
    "utf8",
  ),
) as {
  article: string;
  project: string;
  start: string;
  end: string;
  response: unknown;
  edition: {
    festivalKey: string;
    editionKey: string;
    editionYear: number;
    lineup: Array<{
      artistKey: string;
      billingOrder: number;
      pageviews: number | null;
    }>;
    comparison: {
      festivalKey: string;
      editionKey: string;
      editionYear: number;
      lineupArtistKeys: string[];
    };
    festivalLocation: { latitude: number; longitude: number };
    festivalStartDate: string;
    festivalEndDate: string;
    tourObservations: Array<{
      artistKey: string;
      eventDate: string;
      latitude: number;
      longitude: number;
    }>;
    ticketPrices: Array<{
      marketSide: "primary" | "secondary";
      price: number;
      currency: string;
      sourceSystem: string;
      sourceUrl: string;
    }>;
  };
};

function tempDb(): { path: string; root: string } {
  const root = mkdtempSync(join(tmpdir(), "fb-intelligence-"));
  return { root, path: join(root, "warehouse.duckdb") };
}

describe("intelligence metrics persistence", () => {
  it("upserts attention observations and edition metrics idempotently", async (context) => {
    const temp = tempDb();
    context.after(() => rmSync(temp.root, { recursive: true, force: true }));

    const warehouse = await createDuckDbWarehouse({ path: temp.path });
    await warehouse.ensureReady();
    const duck = warehouse.getDuckDbClient();
    assert.ok(duck);
    const store = new IntelligenceMetricsStore(duck);

    const pageviews = new WikimediaPageviewsClient({
      now: () => new Date("2026-02-01T00:00:00.000Z"),
      fetchImpl: (async () =>
        new Response(JSON.stringify(fixture.response), {
          status: 200,
          headers: { "content-type": "application/json" },
        })) as typeof fetch,
      domainLimits: {
        tokensPerSecond: 100,
        bucketSize: 10,
        minSpacingMs: 0,
        maxRetries: 0,
        baseBackoffMs: 1,
        maxBackoffMs: 1,
      },
    });

    const fetched = await pageviews.fetchPerArticle({
      articleTitle: fixture.article,
      project: fixture.project,
      start: fixture.start,
      end: fixture.end,
      artistKey: "mbid::radiohead",
    });
    const attentionRow = pageviews.toAttentionObservation(fetched, {
      artistKey: "mbid::radiohead",
      festivalKey: fixture.edition.festivalKey,
      editionKey: fixture.edition.editionKey,
      editionYear: fixture.edition.editionYear,
    });

    await store.upsertAttentionObservation(attentionRow);
    await store.upsertAttentionObservation(attentionRow);

    const listed = await store.listAttentionObservations("mbid::radiohead");
    assert.equal(listed.length, 1);
    assert.equal(listed[0]?.status, "ok");
    assert.equal(Number(listed[0]?.value_sum), 3000);

    const metrics = computeEditionAnalyticalMetrics({
      festivalKey: fixture.edition.festivalKey,
      editionKey: fixture.edition.editionKey,
      editionYear: fixture.edition.editionYear,
      lineupArtistKeys: fixture.edition.lineup.map((row) => row.artistKey),
      attention: fixture.edition.lineup.map((row) => ({
        artistKey: row.artistKey,
        value: row.pageviews,
      })),
      billing: fixture.edition.lineup.map((row) => ({
        artistKey: row.artistKey,
        billingOrder: row.billingOrder,
      })),
      comparisonLineupArtistKeys: fixture.edition.comparison.lineupArtistKeys,
      comparisonEditionKey: fixture.edition.comparison.editionKey,
      comparisonFestivalKey: fixture.edition.comparison.festivalKey,
      comparisonYear: fixture.edition.comparison.editionYear,
      festivalLocation: fixture.edition.festivalLocation,
      festivalStartDate: fixture.edition.festivalStartDate,
      festivalEndDate: fixture.edition.festivalEndDate,
      tourObservations: fixture.edition.tourObservations,
      ticketPrices: fixture.edition.ticketPrices,
      computedAt: "2026-02-01T00:00:00.000Z",
    });

    await store.upsertEditionAnalyticalMetrics(metrics);
    await store.upsertEditionAnalyticalMetrics(metrics);

    const stored = await store.getEditionAnalyticalMetrics(
      fixture.edition.editionKey,
      INTELLIGENCE_METRIC_VERSION,
    );
    assert.ok(stored);
    assert.equal(stored.metric_key, metrics.metric_key);
    assert.equal(Number(stored.attention_hhi), metrics.attention_hhi);
    assert.equal(Number(stored.secondary_spread_abs), 251);
    assert.equal(Boolean(stored.attention_missing_flag), true);

    const countRows = await duck.all<{ c: number }>(
      "SELECT COUNT(*) AS c FROM metrics.edition_analytical_metrics",
    );
    assert.equal(Number(countRows[0]?.c), 1);

    await warehouse.close();
  });
});
