/**
 * Idempotent DuckDB persistence for intelligence metric observations.
 * Reuses DuckDbClientLike from the scraper warehouse adapter.
 */
import type { DuckDbClientLike } from "./db";
import type { AttentionObservationRow } from "./attention_sources";
import type { EditionAnalyticalMetrics } from "./intelligence_metrics";

export type TourDateObservationRow = {
  observation_key: string;
  artist_key: string;
  event_date: string;
  venue_name?: string | null;
  city?: string | null;
  region?: string | null;
  country?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  source_system: string;
  source_url?: string | null;
  retrieved_at?: string | null;
  status: "ok" | "error" | "missing";
  error_code?: string | null;
  error_message?: string | null;
  raw_response_json?: unknown;
  metric_version: string;
};

export type TicketPriceObservationRow = {
  observation_key: string;
  festival_key?: string | null;
  edition_key?: string | null;
  edition_year?: number | null;
  market_side: "primary" | "secondary";
  price?: number | null;
  currency?: string | null;
  tier_name?: string | null;
  source_system: string;
  source_url?: string | null;
  retrieved_at?: string | null;
  status: "ok" | "error" | "missing";
  error_code?: string | null;
  error_message?: string | null;
  raw_response_json?: unknown;
  metric_version: string;
};

function jsonOrNull(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  return JSON.stringify(value);
}

export class IntelligenceMetricsStore {
  constructor(private readonly client: DuckDbClientLike) {}

  async upsertAttentionObservation(
    row: AttentionObservationRow,
  ): Promise<void> {
    await this.client.run(
      `
      INSERT INTO metrics.artist_attention_observations (
        observation_key, artist_key, festival_key, edition_key, edition_year,
        source_system, metric_kind, project, access_method, agent, article_title,
        granularity, period_start, period_end, value, value_sum, value_unit,
        status, error_code, error_message, source_url, retrieved_at,
        raw_response_json, provenance_json, metric_version, ingested_at
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
      )
      ON CONFLICT (observation_key) DO UPDATE SET
        festival_key = excluded.festival_key,
        edition_key = excluded.edition_key,
        edition_year = excluded.edition_year,
        value = excluded.value,
        value_sum = excluded.value_sum,
        value_unit = excluded.value_unit,
        status = excluded.status,
        error_code = excluded.error_code,
        error_message = excluded.error_message,
        source_url = excluded.source_url,
        retrieved_at = excluded.retrieved_at,
        raw_response_json = excluded.raw_response_json,
        provenance_json = excluded.provenance_json,
        metric_version = excluded.metric_version,
        ingested_at = excluded.ingested_at
      `,
      row.observation_key,
      row.artist_key,
      row.festival_key,
      row.edition_key,
      row.edition_year,
      row.source_system,
      row.metric_kind,
      row.project,
      row.access_method,
      row.agent,
      row.article_title,
      row.granularity,
      row.period_start,
      row.period_end,
      row.value,
      row.value_sum,
      row.value_unit,
      row.status,
      row.error_code,
      row.error_message,
      row.source_url,
      row.retrieved_at,
      jsonOrNull(row.raw_response_json),
      jsonOrNull(row.provenance_json),
      row.metric_version,
    );
  }

  async upsertTourDateObservation(row: TourDateObservationRow): Promise<void> {
    await this.client.run(
      `
      INSERT INTO metrics.tour_date_observations (
        observation_key, artist_key, event_date, venue_name, city, region, country,
        latitude, longitude, source_system, source_url, retrieved_at, status,
        error_code, error_message, raw_response_json, metric_version, ingested_at
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
      )
      ON CONFLICT (observation_key) DO UPDATE SET
        venue_name = excluded.venue_name,
        city = excluded.city,
        region = excluded.region,
        country = excluded.country,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        source_url = excluded.source_url,
        retrieved_at = excluded.retrieved_at,
        status = excluded.status,
        error_code = excluded.error_code,
        error_message = excluded.error_message,
        raw_response_json = excluded.raw_response_json,
        metric_version = excluded.metric_version,
        ingested_at = excluded.ingested_at
      `,
      row.observation_key,
      row.artist_key,
      row.event_date,
      row.venue_name ?? null,
      row.city ?? null,
      row.region ?? null,
      row.country ?? null,
      row.latitude ?? null,
      row.longitude ?? null,
      row.source_system,
      row.source_url ?? null,
      row.retrieved_at ?? null,
      row.status,
      row.error_code ?? null,
      row.error_message ?? null,
      jsonOrNull(row.raw_response_json),
      row.metric_version,
    );
  }

  async upsertTicketPriceObservation(
    row: TicketPriceObservationRow,
  ): Promise<void> {
    await this.client.run(
      `
      INSERT INTO metrics.ticket_price_observations (
        observation_key, festival_key, edition_key, edition_year, market_side,
        price, currency, tier_name, source_system, source_url, retrieved_at,
        status, error_code, error_message, raw_response_json, metric_version,
        ingested_at
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
      )
      ON CONFLICT (observation_key) DO UPDATE SET
        price = excluded.price,
        currency = excluded.currency,
        tier_name = excluded.tier_name,
        source_url = excluded.source_url,
        retrieved_at = excluded.retrieved_at,
        status = excluded.status,
        error_code = excluded.error_code,
        error_message = excluded.error_message,
        raw_response_json = excluded.raw_response_json,
        metric_version = excluded.metric_version,
        ingested_at = excluded.ingested_at
      `,
      row.observation_key,
      row.festival_key ?? null,
      row.edition_key ?? null,
      row.edition_year ?? null,
      row.market_side,
      row.price ?? null,
      row.currency ?? null,
      row.tier_name ?? null,
      row.source_system,
      row.source_url ?? null,
      row.retrieved_at ?? null,
      row.status,
      row.error_code ?? null,
      row.error_message ?? null,
      jsonOrNull(row.raw_response_json),
      row.metric_version,
    );
  }

  async upsertEditionAnalyticalMetrics(
    row: EditionAnalyticalMetrics,
  ): Promise<void> {
    await this.client.run(
      `
      INSERT INTO metrics.edition_analytical_metrics (
        metric_key, festival_key, edition_key, edition_year, metric_version,
        attention_hhi, attention_share_json, attention_artist_count,
        attention_coverage_ratio, attention_missing_flag,
        billing_arbitrage_score, billing_arbitrage_spearman,
        billing_arbitrage_coverage_ratio, billing_arbitrage_missing_flag,
        promoter_shared_inventory_jaccard, promoter_comparison_edition_key,
        promoter_comparison_festival_key, promoter_comparison_year,
        promoter_jaccard_missing_flag,
        exclusivity_gap_km, exclusivity_conflict_count, exclusivity_radius_km,
        exclusivity_window_days, exclusivity_missing_flag,
        secondary_spread_abs, secondary_spread_pct, primary_price, secondary_price,
        primary_currency, secondary_currency, secondary_spread_missing_flag,
        input_hash, evidence_json, flags_json, computed_at, ingested_at
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
      )
      ON CONFLICT (metric_key) DO UPDATE SET
        attention_hhi = excluded.attention_hhi,
        attention_share_json = excluded.attention_share_json,
        attention_artist_count = excluded.attention_artist_count,
        attention_coverage_ratio = excluded.attention_coverage_ratio,
        attention_missing_flag = excluded.attention_missing_flag,
        billing_arbitrage_score = excluded.billing_arbitrage_score,
        billing_arbitrage_spearman = excluded.billing_arbitrage_spearman,
        billing_arbitrage_coverage_ratio = excluded.billing_arbitrage_coverage_ratio,
        billing_arbitrage_missing_flag = excluded.billing_arbitrage_missing_flag,
        promoter_shared_inventory_jaccard = excluded.promoter_shared_inventory_jaccard,
        promoter_comparison_edition_key = excluded.promoter_comparison_edition_key,
        promoter_comparison_festival_key = excluded.promoter_comparison_festival_key,
        promoter_comparison_year = excluded.promoter_comparison_year,
        promoter_jaccard_missing_flag = excluded.promoter_jaccard_missing_flag,
        exclusivity_gap_km = excluded.exclusivity_gap_km,
        exclusivity_conflict_count = excluded.exclusivity_conflict_count,
        exclusivity_radius_km = excluded.exclusivity_radius_km,
        exclusivity_window_days = excluded.exclusivity_window_days,
        exclusivity_missing_flag = excluded.exclusivity_missing_flag,
        secondary_spread_abs = excluded.secondary_spread_abs,
        secondary_spread_pct = excluded.secondary_spread_pct,
        primary_price = excluded.primary_price,
        secondary_price = excluded.secondary_price,
        primary_currency = excluded.primary_currency,
        secondary_currency = excluded.secondary_currency,
        secondary_spread_missing_flag = excluded.secondary_spread_missing_flag,
        input_hash = excluded.input_hash,
        evidence_json = excluded.evidence_json,
        flags_json = excluded.flags_json,
        computed_at = excluded.computed_at,
        ingested_at = excluded.ingested_at
      `,
      row.metric_key,
      row.festival_key,
      row.edition_key,
      row.edition_year,
      row.metric_version,
      row.attention_hhi,
      jsonOrNull(row.attention_share_json),
      row.attention_artist_count,
      row.attention_coverage_ratio,
      row.attention_missing_flag,
      row.billing_arbitrage_score,
      row.billing_arbitrage_spearman,
      row.billing_arbitrage_coverage_ratio,
      row.billing_arbitrage_missing_flag,
      row.promoter_shared_inventory_jaccard,
      row.promoter_comparison_edition_key,
      row.promoter_comparison_festival_key,
      row.promoter_comparison_year,
      row.promoter_jaccard_missing_flag,
      row.exclusivity_gap_km,
      row.exclusivity_conflict_count,
      row.exclusivity_radius_km,
      row.exclusivity_window_days,
      row.exclusivity_missing_flag,
      row.secondary_spread_abs,
      row.secondary_spread_pct,
      row.primary_price,
      row.secondary_price,
      row.primary_currency,
      row.secondary_currency,
      row.secondary_spread_missing_flag,
      row.input_hash,
      jsonOrNull(row.evidence_json),
      jsonOrNull(row.flags_json),
      row.computed_at,
    );
  }

  async getEditionAnalyticalMetrics(
    editionKey: string,
    metricVersion: string,
  ): Promise<Record<string, unknown> | null> {
    const rows = await this.client.all<Record<string, unknown>>(
      `
      SELECT *
      FROM metrics.edition_analytical_metrics
      WHERE edition_key = ? AND metric_version = ?
      ORDER BY computed_at DESC
      LIMIT 1
      `,
      editionKey,
      metricVersion,
    );
    return rows[0] ?? null;
  }

  async listAttentionObservations(
    artistKey: string,
  ): Promise<Record<string, unknown>[]> {
    return this.client.all<Record<string, unknown>>(
      `
      SELECT *
      FROM metrics.artist_attention_observations
      WHERE artist_key = ?
      ORDER BY retrieved_at DESC
      `,
      artistKey,
    );
  }
}
