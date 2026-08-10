import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, it } from "node:test";
import {
  computeAttentionShareAndHhi,
  computeBillingArbitrage,
  computeEditionAnalyticalMetrics,
  computeExclusivityGap,
  computeSecondaryMarketSpread,
  computeSharedInventoryJaccard,
} from "../../src/scraper/intelligence_metrics";
import { buildSpotifyAttentionObservation } from "../../src/scraper/attention_sources";

const fixture = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../tests/fixtures/intelligence/pageviews_radiohead.json"),
    "utf8",
  ),
) as {
  edition: {
    festivalKey: string;
    editionKey: string;
    editionYear: number;
    lineup: Array<{
      artistKey: string;
      artistName: string;
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

describe("intelligence metrics computations", () => {
  it("computes attention shares and HHI without fabricating missing artists", () => {
    const lineup = fixture.edition.lineup.map((row) => row.artistKey);
    const attention = fixture.edition.lineup.map((row) => ({
      artistKey: row.artistKey,
      value: row.pageviews,
    }));
    const result = computeAttentionShareAndHhi(lineup, attention);
    assert.equal(result.artistCount, 2);
    assert.ok(result.missingFlag);
    assert.deepEqual(result.missingArtistKeys, ["name::emerging-act"]);
    assert.equal(result.shares["mbid::radiohead"], 0.25);
    assert.equal(result.shares["mbid::beyonce"], 0.75);
    assert.equal(result.hhi, 0.25 ** 2 + 0.75 ** 2);
    assert.equal(result.coverageRatio, 2 / 3);
  });

  it("returns null HHI when no attention evidence exists", () => {
    const result = computeAttentionShareAndHhi(["a", "b"], [
      { artistKey: "a", value: null },
    ]);
    assert.equal(result.hhi, null);
    assert.equal(result.missingFlag, true);
  });

  it("computes billing arbitrage with explicit missing-data handling", () => {
    const billing = fixture.edition.lineup.map((row) => ({
      artistKey: row.artistKey,
      billingOrder: row.billingOrder,
    }));
    const attention = fixture.edition.lineup.map((row) => ({
      artistKey: row.artistKey,
      value: row.pageviews,
    }));
    const result = computeBillingArbitrage(billing, attention);
    assert.equal(result.pairedCount, 2);
    assert.ok(result.missingFlag);
    assert.equal(result.score, 0);
    // Billing order and cultural velocity are perfectly inverted for the paired artists.
    assert.equal(result.spearman, -1);
    assert.deepEqual(result.missingArtistKeys, ["name::emerging-act"]);
  });

  it("computes promoter shared-inventory Jaccard", () => {
    const left = fixture.edition.lineup.map((row) => row.artistKey);
    const right = fixture.edition.comparison.lineupArtistKeys;
    const result = computeSharedInventoryJaccard(left, right);
    // intersection: radiohead, beyonce = 2; union = 4 → 0.5
    assert.equal(result.jaccard, 0.5);
    assert.equal(result.missingFlag, false);
    assert.equal(
      computeSharedInventoryJaccard([], right).jaccard,
      null,
    );
  });

  it("detects exclusivity/radius gaps from dated tour observations", () => {
    const result = computeExclusivityGap({
      lineupArtistKeys: fixture.edition.lineup.map((row) => row.artistKey),
      festivalLocation: fixture.edition.festivalLocation,
      festivalStartDate: fixture.edition.festivalStartDate,
      festivalEndDate: fixture.edition.festivalEndDate,
      tourObservations: fixture.edition.tourObservations,
      radiusKm: 250,
      windowDays: 14,
    });
    assert.equal(result.missingFlag, false);
    assert.equal(result.conflictCount, 1);
    assert.ok(result.gapKm != null && result.gapKm < 250);

    const missing = computeExclusivityGap({
      lineupArtistKeys: ["mbid::radiohead"],
      festivalLocation: null,
      festivalStartDate: null,
      festivalEndDate: null,
      tourObservations: [],
    });
    assert.equal(missing.gapKm, null);
    assert.equal(missing.missingFlag, true);
  });

  it("computes secondary-market spread with currency provenance guards", () => {
    const ok = computeSecondaryMarketSpread(fixture.edition.ticketPrices);
    assert.equal(ok.missingFlag, false);
    assert.equal(ok.spreadAbs, 251);
    assert.ok(ok.spreadPct != null);
    assert.equal(ok.primaryCurrency, "USD");
    assert.equal(
      ok.provenance.primarySourceUrl,
      "https://example.com/primary",
    );

    const mismatch = computeSecondaryMarketSpread([
      { marketSide: "primary", price: 100, currency: "USD" },
      { marketSide: "secondary", price: 120, currency: "EUR" },
    ]);
    assert.equal(mismatch.spreadAbs, null);
    assert.equal(mismatch.missingFlag, true);
    assert.equal(mismatch.missingReason, "currency_mismatch");
  });

  it("composes edition analytical metrics deterministically", () => {
    const edition = fixture.edition;
    const first = computeEditionAnalyticalMetrics({
      festivalKey: edition.festivalKey,
      editionKey: edition.editionKey,
      editionYear: edition.editionYear,
      lineupArtistKeys: edition.lineup.map((row) => row.artistKey),
      attention: edition.lineup.map((row) => ({
        artistKey: row.artistKey,
        value: row.pageviews,
      })),
      billing: edition.lineup.map((row) => ({
        artistKey: row.artistKey,
        billingOrder: row.billingOrder,
      })),
      comparisonLineupArtistKeys: edition.comparison.lineupArtistKeys,
      comparisonEditionKey: edition.comparison.editionKey,
      comparisonFestivalKey: edition.comparison.festivalKey,
      comparisonYear: edition.comparison.editionYear,
      festivalLocation: edition.festivalLocation,
      festivalStartDate: edition.festivalStartDate,
      festivalEndDate: edition.festivalEndDate,
      tourObservations: edition.tourObservations,
      ticketPrices: edition.ticketPrices,
      computedAt: "2026-02-01T00:00:00.000Z",
    });
    const second = computeEditionAnalyticalMetrics({
      festivalKey: edition.festivalKey,
      editionKey: edition.editionKey,
      editionYear: edition.editionYear,
      lineupArtistKeys: edition.lineup.map((row) => row.artistKey),
      attention: edition.lineup.map((row) => ({
        artistKey: row.artistKey,
        value: row.pageviews,
      })),
      billing: edition.lineup.map((row) => ({
        artistKey: row.artistKey,
        billingOrder: row.billingOrder,
      })),
      comparisonLineupArtistKeys: edition.comparison.lineupArtistKeys,
      comparisonEditionKey: edition.comparison.editionKey,
      comparisonFestivalKey: edition.comparison.festivalKey,
      comparisonYear: edition.comparison.editionYear,
      festivalLocation: edition.festivalLocation,
      festivalStartDate: edition.festivalStartDate,
      festivalEndDate: edition.festivalEndDate,
      tourObservations: edition.tourObservations,
      ticketPrices: edition.ticketPrices,
      computedAt: "2026-02-01T00:00:00.000Z",
    });

    assert.equal(first.metric_key, second.metric_key);
    assert.equal(first.input_hash, second.input_hash);
    assert.equal(first.attention_hhi, 0.25 ** 2 + 0.75 ** 2);
    assert.equal(first.promoter_shared_inventory_jaccard, 0.5);
    assert.equal(first.secondary_spread_abs, 251);
    assert.equal(first.attention_missing_flag, true);
    assert.equal(first.exclusivity_missing_flag, false);
  });

  it("builds a Spotify fallback observation foundation without inventing values", () => {
    const missing = buildSpotifyAttentionObservation(
      {
        artistKey: "mbid::radiohead",
        artistName: "Radiohead",
        festivalKey: "coachella",
        editionKey: "coachella_2026",
        editionYear: 2026,
      },
      null,
      {
        retrievedAt: "2026-02-01T00:00:00.000Z",
        status: "missing",
        errorCode: "spotify_client_unavailable",
        errorMessage: "no credentials",
      },
    );
    assert.equal(missing.source_system, "spotify");
    assert.equal(missing.value, null);
    assert.equal(missing.status, "missing");
    assert.equal(missing.error_code, "spotify_client_unavailable");
  });
});
