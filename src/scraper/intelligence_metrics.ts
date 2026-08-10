/**
 * Deterministic Festival-Bloomberg intelligence metric computations.
 *
 * Metrics remain null/flagged when evidence is unavailable — never fabricate.
 * Pure functions only; persistence lives in intelligence_store / repository.
 */
import { createHash } from "node:crypto";
import { INTELLIGENCE_METRIC_VERSION } from "./wikimedia_pageviews";

export type AttentionValue = {
  artistKey: string;
  value: number | null | undefined;
};

export type AttentionShareResult = {
  shares: Record<string, number>;
  hhi: number | null;
  artistCount: number;
  coverageRatio: number;
  missingFlag: boolean;
  missingArtistKeys: string[];
};

export type BillingSlot = {
  artistKey: string;
  /** Poster billing rank/order; lower = higher billing prominence. */
  billingOrder: number | null | undefined;
};

export type BillingArbitrageResult = {
  score: number | null;
  spearman: number | null;
  coverageRatio: number;
  missingFlag: boolean;
  pairedCount: number;
  missingArtistKeys: string[];
};

export type JaccardResult = {
  jaccard: number | null;
  intersectionSize: number;
  unionSize: number;
  missingFlag: boolean;
};

export type GeoPoint = {
  latitude: number;
  longitude: number;
};

export type TourObservation = {
  artistKey: string;
  eventDate: string; // YYYY-MM-DD
  latitude: number | null | undefined;
  longitude: number | null | undefined;
};

export type ExclusivityGapResult = {
  gapKm: number | null;
  conflictCount: number;
  radiusKm: number;
  windowDays: number;
  missingFlag: boolean;
  conflicts: Array<{
    artistKey: string;
    eventDate: string;
    distanceKm: number;
  }>;
};

export type TicketPricePoint = {
  marketSide: "primary" | "secondary";
  price: number | null | undefined;
  currency: string | null | undefined;
  sourceSystem?: string;
  sourceUrl?: string;
};

export type SecondarySpreadResult = {
  spreadAbs: number | null;
  spreadPct: number | null;
  primaryPrice: number | null;
  secondaryPrice: number | null;
  primaryCurrency: string | null;
  secondaryCurrency: string | null;
  missingFlag: boolean;
  missingReason: string | null;
  provenance: {
    primarySourceSystem?: string;
    primarySourceUrl?: string;
    secondarySourceSystem?: string;
    secondarySourceUrl?: string;
  };
};

export type EditionMetricInputs = {
  festivalKey: string;
  editionKey: string;
  editionYear: number;
  lineupArtistKeys: string[];
  attention: AttentionValue[];
  billing: BillingSlot[];
  comparisonLineupArtistKeys?: string[];
  comparisonEditionKey?: string;
  comparisonFestivalKey?: string;
  comparisonYear?: number;
  festivalLocation?: GeoPoint | null;
  festivalStartDate?: string | null;
  festivalEndDate?: string | null;
  tourObservations?: TourObservation[];
  exclusivityRadiusKm?: number;
  exclusivityWindowDays?: number;
  ticketPrices?: TicketPricePoint[];
  metricVersion?: string;
  computedAt?: string;
};

export type EditionAnalyticalMetrics = {
  metric_key: string;
  festival_key: string;
  edition_key: string;
  edition_year: number;
  metric_version: string;

  attention_hhi: number | null;
  attention_share_json: Record<string, number> | null;
  attention_artist_count: number;
  attention_coverage_ratio: number;
  attention_missing_flag: boolean;

  billing_arbitrage_score: number | null;
  billing_arbitrage_spearman: number | null;
  billing_arbitrage_coverage_ratio: number;
  billing_arbitrage_missing_flag: boolean;

  promoter_shared_inventory_jaccard: number | null;
  promoter_comparison_edition_key: string | null;
  promoter_comparison_festival_key: string | null;
  promoter_comparison_year: number | null;
  promoter_jaccard_missing_flag: boolean;

  exclusivity_gap_km: number | null;
  exclusivity_conflict_count: number | null;
  exclusivity_radius_km: number | null;
  exclusivity_window_days: number | null;
  exclusivity_missing_flag: boolean;

  secondary_spread_abs: number | null;
  secondary_spread_pct: number | null;
  primary_price: number | null;
  secondary_price: number | null;
  primary_currency: string | null;
  secondary_currency: string | null;
  secondary_spread_missing_flag: boolean;

  input_hash: string;
  evidence_json: Record<string, unknown>;
  flags_json: Record<string, boolean | string | null>;
  computed_at: string;
};

/** Attention-share vector and Herfindahl–Hirschman Index (sum of squared shares). */
export function computeAttentionShareAndHhi(
  lineupArtistKeys: string[],
  attention: AttentionValue[],
): AttentionShareResult {
  const valueByArtist = new Map<string, number>();
  for (const row of attention) {
    if (row.value == null || !Number.isFinite(row.value) || row.value < 0) {
      continue;
    }
    valueByArtist.set(row.artistKey, row.value);
  }

  const missingArtistKeys = lineupArtistKeys.filter(
    (key) => !valueByArtist.has(key),
  );
  const covered = lineupArtistKeys.filter((key) => valueByArtist.has(key));
  const coverageRatio =
    lineupArtistKeys.length === 0 ? 0 : covered.length / lineupArtistKeys.length;

  if (covered.length === 0) {
    return {
      shares: {},
      hhi: null,
      artistCount: 0,
      coverageRatio,
      missingFlag: true,
      missingArtistKeys,
    };
  }

  const total = covered.reduce(
    (sum, key) => sum + (valueByArtist.get(key) as number),
    0,
  );
  if (!(total > 0)) {
    return {
      shares: {},
      hhi: null,
      artistCount: covered.length,
      coverageRatio,
      missingFlag: true,
      missingArtistKeys,
    };
  }

  const shares: Record<string, number> = {};
  let hhi = 0;
  for (const key of covered) {
    const share = (valueByArtist.get(key) as number) / total;
    shares[key] = share;
    hhi += share * share;
  }

  return {
    shares,
    hhi,
    artistCount: covered.length,
    coverageRatio,
    missingFlag: missingArtistKeys.length > 0,
    missingArtistKeys,
  };
}

/**
 * Billing arbitrage: compare poster billing order to cultural-velocity ranks.
 * Score is mean(attentionRank - billingRank) / n after converting both to
 * 1..n ranks (billingOrder ascending = better billing; attention descending).
 * Spearman uses the same paired ranks. Missing either side excludes the artist.
 */
export function computeBillingArbitrage(
  billing: BillingSlot[],
  attention: AttentionValue[],
): BillingArbitrageResult {
  const attentionByArtist = new Map<string, number>();
  for (const row of attention) {
    if (row.value == null || !Number.isFinite(row.value) || row.value < 0) {
      continue;
    }
    attentionByArtist.set(row.artistKey, row.value);
  }

  const pairs: Array<{ artistKey: string; billingOrder: number; attention: number }> =
    [];
  const missingArtistKeys: string[] = [];

  for (const slot of billing) {
    const hasBilling =
      slot.billingOrder != null && Number.isFinite(slot.billingOrder);
    const hasAttention = attentionByArtist.has(slot.artistKey);
    if (!hasBilling || !hasAttention) {
      missingArtistKeys.push(slot.artistKey);
      continue;
    }
    pairs.push({
      artistKey: slot.artistKey,
      billingOrder: slot.billingOrder as number,
      attention: attentionByArtist.get(slot.artistKey) as number,
    });
  }

  const coverageRatio =
    billing.length === 0 ? 0 : pairs.length / billing.length;

  if (pairs.length < 2) {
    return {
      score: null,
      spearman: null,
      coverageRatio,
      missingFlag: true,
      pairedCount: pairs.length,
      missingArtistKeys,
    };
  }

  const billingRanks = denseRank(
    pairs.map((p) => p.billingOrder),
    "asc",
  );
  const attentionRanks = denseRank(
    pairs.map((p) => p.attention),
    "desc",
  );

  let scoreSum = 0;
  for (let i = 0; i < pairs.length; i += 1) {
    scoreSum += attentionRanks[i] - billingRanks[i];
  }
  const score = scoreSum / pairs.length;
  const spearman = spearmanFromRanks(billingRanks, attentionRanks);

  return {
    score,
    spearman,
    coverageRatio,
    missingFlag: missingArtistKeys.length > 0,
    pairedCount: pairs.length,
    missingArtistKeys,
  };
}

/** Jaccard similarity of artist inventories across festivals/years. */
export function computeSharedInventoryJaccard(
  leftArtistKeys: string[],
  rightArtistKeys: string[],
): JaccardResult {
  if (leftArtistKeys.length === 0 || rightArtistKeys.length === 0) {
    return {
      jaccard: null,
      intersectionSize: 0,
      unionSize: 0,
      missingFlag: true,
    };
  }
  const left = new Set(leftArtistKeys);
  const right = new Set(rightArtistKeys);
  let intersectionSize = 0;
  for (const key of left) {
    if (right.has(key)) intersectionSize += 1;
  }
  const unionSize = left.size + right.size - intersectionSize;
  if (unionSize === 0) {
    return {
      jaccard: null,
      intersectionSize: 0,
      unionSize: 0,
      missingFlag: true,
    };
  }
  return {
    jaccard: intersectionSize / unionSize,
    intersectionSize,
    unionSize,
    missingFlag: false,
  };
}

/**
 * Detect exclusivity / radius gaps from public dated tour observations near a
 * festival. Returns the minimum conflicting distance (gap) when any tour date
 * for a lineup artist falls inside the radius and date window.
 */
export function computeExclusivityGap(input: {
  lineupArtistKeys: string[];
  festivalLocation?: GeoPoint | null;
  festivalStartDate?: string | null;
  festivalEndDate?: string | null;
  tourObservations: TourObservation[];
  radiusKm?: number;
  windowDays?: number;
}): ExclusivityGapResult {
  const radiusKm = input.radiusKm ?? 250;
  const windowDays = input.windowDays ?? 14;
  const location = input.festivalLocation;
  const start = input.festivalStartDate;
  const end = input.festivalEndDate;

  if (
    !location ||
    !Number.isFinite(location.latitude) ||
    !Number.isFinite(location.longitude) ||
    !start ||
    !end
  ) {
    return {
      gapKm: null,
      conflictCount: 0,
      radiusKm,
      windowDays,
      missingFlag: true,
      conflicts: [],
    };
  }

  const lineup = new Set(input.lineupArtistKeys);
  const windowStart = addDays(start, -windowDays);
  const windowEnd = addDays(end, windowDays);
  if (!windowStart || !windowEnd) {
    return {
      gapKm: null,
      conflictCount: 0,
      radiusKm,
      windowDays,
      missingFlag: true,
      conflicts: [],
    };
  }

  const usable = input.tourObservations.filter((obs) => {
    if (!lineup.has(obs.artistKey)) return false;
    if (obs.latitude == null || obs.longitude == null) return false;
    if (!Number.isFinite(obs.latitude) || !Number.isFinite(obs.longitude)) {
      return false;
    }
    return obs.eventDate >= windowStart && obs.eventDate <= windowEnd;
  });

  if (usable.length === 0) {
    return {
      gapKm: null,
      conflictCount: 0,
      radiusKm,
      windowDays,
      missingFlag: true,
      conflicts: [],
    };
  }

  const conflicts: ExclusivityGapResult["conflicts"] = [];
  for (const obs of usable) {
    const distanceKm = haversineKm(
      location.latitude,
      location.longitude,
      obs.latitude as number,
      obs.longitude as number,
    );
    if (distanceKm <= radiusKm) {
      conflicts.push({
        artistKey: obs.artistKey,
        eventDate: obs.eventDate,
        distanceKm,
      });
    }
  }

  if (conflicts.length === 0) {
    return {
      gapKm: null,
      conflictCount: 0,
      radiusKm,
      windowDays,
      missingFlag: false,
      conflicts: [],
    };
  }

  conflicts.sort((a, b) => a.distanceKm - b.distanceKm);
  return {
    gapKm: conflicts[0].distanceKm,
    conflictCount: conflicts.length,
    radiusKm,
    windowDays,
    missingFlag: false,
    conflicts,
  };
}

/**
 * Secondary-market spread vs primary. Requires both sides with finite prices
 * and matching currencies; otherwise null + flagged (no FX invention).
 */
export function computeSecondaryMarketSpread(
  prices: TicketPricePoint[],
): SecondarySpreadResult {
  const primary = pickPrice(prices, "primary");
  const secondary = pickPrice(prices, "secondary");

  if (!primary || !secondary) {
    return {
      spreadAbs: null,
      spreadPct: null,
      primaryPrice: primary?.price ?? null,
      secondaryPrice: secondary?.price ?? null,
      primaryCurrency: primary?.currency ?? null,
      secondaryCurrency: secondary?.currency ?? null,
      missingFlag: true,
      missingReason: !primary
        ? "missing_primary_price"
        : "missing_secondary_price",
      provenance: {
        primarySourceSystem: primary?.sourceSystem,
        primarySourceUrl: primary?.sourceUrl,
        secondarySourceSystem: secondary?.sourceSystem,
        secondarySourceUrl: secondary?.sourceUrl,
      },
    };
  }

  const primaryCurrency = normalizeCurrency(primary.currency);
  const secondaryCurrency = normalizeCurrency(secondary.currency);
  if (!primaryCurrency || !secondaryCurrency) {
    return {
      spreadAbs: null,
      spreadPct: null,
      primaryPrice: primary.price,
      secondaryPrice: secondary.price,
      primaryCurrency,
      secondaryCurrency,
      missingFlag: true,
      missingReason: "missing_currency",
      provenance: {
        primarySourceSystem: primary.sourceSystem,
        primarySourceUrl: primary.sourceUrl,
        secondarySourceSystem: secondary.sourceSystem,
        secondarySourceUrl: secondary.sourceUrl,
      },
    };
  }

  if (primaryCurrency !== secondaryCurrency) {
    return {
      spreadAbs: null,
      spreadPct: null,
      primaryPrice: primary.price,
      secondaryPrice: secondary.price,
      primaryCurrency,
      secondaryCurrency,
      missingFlag: true,
      missingReason: "currency_mismatch",
      provenance: {
        primarySourceSystem: primary.sourceSystem,
        primarySourceUrl: primary.sourceUrl,
        secondarySourceSystem: secondary.sourceSystem,
        secondarySourceUrl: secondary.sourceUrl,
      },
    };
  }

  const spreadAbs = secondary.price - primary.price;
  const spreadPct = primary.price === 0 ? null : spreadAbs / primary.price;

  return {
    spreadAbs,
    spreadPct,
    primaryPrice: primary.price,
    secondaryPrice: secondary.price,
    primaryCurrency,
    secondaryCurrency,
    missingFlag: false,
    missingReason: null,
    provenance: {
      primarySourceSystem: primary.sourceSystem,
      primarySourceUrl: primary.sourceUrl,
      secondarySourceSystem: secondary.sourceSystem,
      secondarySourceUrl: secondary.sourceUrl,
    },
  };
}

/** Compose all edition analytical metrics from available evidence. */
export function computeEditionAnalyticalMetrics(
  input: EditionMetricInputs,
): EditionAnalyticalMetrics {
  const metricVersion = input.metricVersion ?? INTELLIGENCE_METRIC_VERSION;
  const computedAt = input.computedAt ?? new Date().toISOString();

  const attention = computeAttentionShareAndHhi(
    input.lineupArtistKeys,
    input.attention,
  );
  const billing = computeBillingArbitrage(input.billing, input.attention);
  const hasComparison =
    Array.isArray(input.comparisonLineupArtistKeys) &&
    input.comparisonEditionKey != null;
  const jaccard = hasComparison
    ? computeSharedInventoryJaccard(
        input.lineupArtistKeys,
        input.comparisonLineupArtistKeys as string[],
      )
    : {
        jaccard: null,
        intersectionSize: 0,
        unionSize: 0,
        missingFlag: true,
      };

  const exclusivity = computeExclusivityGap({
    lineupArtistKeys: input.lineupArtistKeys,
    festivalLocation: input.festivalLocation,
    festivalStartDate: input.festivalStartDate,
    festivalEndDate: input.festivalEndDate,
    tourObservations: input.tourObservations ?? [],
    radiusKm: input.exclusivityRadiusKm,
    windowDays: input.exclusivityWindowDays,
  });

  const spread = computeSecondaryMarketSpread(input.ticketPrices ?? []);

  const metricKey = editionMetricKey({
    editionKey: input.editionKey,
    metricVersion,
    comparisonEditionKey: input.comparisonEditionKey,
  });

  const evidence = {
    attentionMissingArtistKeys: attention.missingArtistKeys,
    billingMissingArtistKeys: billing.missingArtistKeys,
    jaccardIntersectionSize: jaccard.intersectionSize,
    jaccardUnionSize: jaccard.unionSize,
    exclusivityConflicts: exclusivity.conflicts,
    secondarySpreadProvenance: spread.provenance,
    secondarySpreadMissingReason: spread.missingReason,
  };

  const inputHash = createHash("sha256")
    .update(
      JSON.stringify({
        festivalKey: input.festivalKey,
        editionKey: input.editionKey,
        editionYear: input.editionYear,
        lineupArtistKeys: [...input.lineupArtistKeys].sort(),
        attention: [...input.attention].sort((a, b) =>
          a.artistKey.localeCompare(b.artistKey),
        ),
        billing: [...input.billing].sort((a, b) =>
          a.artistKey.localeCompare(b.artistKey),
        ),
        comparisonEditionKey: input.comparisonEditionKey ?? null,
        comparisonLineupArtistKeys: [
          ...(input.comparisonLineupArtistKeys ?? []),
        ].sort(),
        metricVersion,
      }),
    )
    .digest("hex");

  return {
    metric_key: metricKey,
    festival_key: input.festivalKey,
    edition_key: input.editionKey,
    edition_year: input.editionYear,
    metric_version: metricVersion,

    attention_hhi: attention.hhi,
    attention_share_json:
      Object.keys(attention.shares).length > 0 ? attention.shares : null,
    attention_artist_count: attention.artistCount,
    attention_coverage_ratio: attention.coverageRatio,
    attention_missing_flag: attention.missingFlag,

    billing_arbitrage_score: billing.score,
    billing_arbitrage_spearman: billing.spearman,
    billing_arbitrage_coverage_ratio: billing.coverageRatio,
    billing_arbitrage_missing_flag: billing.missingFlag,

    promoter_shared_inventory_jaccard: jaccard.jaccard,
    promoter_comparison_edition_key: input.comparisonEditionKey ?? null,
    promoter_comparison_festival_key: input.comparisonFestivalKey ?? null,
    promoter_comparison_year: input.comparisonYear ?? null,
    promoter_jaccard_missing_flag: jaccard.missingFlag,

    exclusivity_gap_km: exclusivity.gapKm,
    exclusivity_conflict_count: exclusivity.conflictCount,
    exclusivity_radius_km: exclusivity.radiusKm,
    exclusivity_window_days: exclusivity.windowDays,
    exclusivity_missing_flag: exclusivity.missingFlag,

    secondary_spread_abs: spread.spreadAbs,
    secondary_spread_pct: spread.spreadPct,
    primary_price: spread.primaryPrice,
    secondary_price: spread.secondaryPrice,
    primary_currency: spread.primaryCurrency,
    secondary_currency: spread.secondaryCurrency,
    secondary_spread_missing_flag: spread.missingFlag,

    input_hash: inputHash,
    evidence_json: evidence,
    flags_json: {
      attention_missing_flag: attention.missingFlag,
      billing_arbitrage_missing_flag: billing.missingFlag,
      promoter_jaccard_missing_flag: jaccard.missingFlag,
      exclusivity_missing_flag: exclusivity.missingFlag,
      secondary_spread_missing_flag: spread.missingFlag,
      secondary_spread_missing_reason: spread.missingReason,
    },
    computed_at: computedAt,
  };
}

export function editionMetricKey(parts: {
  editionKey: string;
  metricVersion: string;
  comparisonEditionKey?: string | null;
}): string {
  const material = [
    parts.editionKey,
    parts.metricVersion,
    parts.comparisonEditionKey ?? "",
  ].join("|");
  return createHash("sha256").update(material).digest("hex").slice(0, 32);
}

function denseRank(values: number[], direction: "asc" | "desc"): number[] {
  const indexed = values.map((value, index) => ({ value, index }));
  indexed.sort((a, b) =>
    direction === "asc" ? a.value - b.value : b.value - a.value,
  );
  const ranks = new Array<number>(values.length);
  let rank = 1;
  for (let i = 0; i < indexed.length; i += 1) {
    if (i > 0 && indexed[i].value !== indexed[i - 1].value) {
      rank = i + 1;
    }
    ranks[indexed[i].index] = rank;
  }
  return ranks;
}

function spearmanFromRanks(left: number[], right: number[]): number | null {
  const n = left.length;
  if (n < 2) return null;
  let sumD2 = 0;
  for (let i = 0; i < n; i += 1) {
    const d = left[i] - right[i];
    sumD2 += d * d;
  }
  return 1 - (6 * sumD2) / (n * (n * n - 1));
}

function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const r = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

function addDays(isoDate: string, days: number): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return null;
  const date = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  );
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function normalizeCurrency(currency: string | null | undefined): string | null {
  if (!currency) return null;
  const normalized = currency.trim().toUpperCase();
  return normalized.length > 0 ? normalized : null;
}

function pickPrice(
  prices: TicketPricePoint[],
  side: "primary" | "secondary",
): { price: number; currency: string | null | undefined; sourceSystem?: string; sourceUrl?: string } | null {
  for (const row of prices) {
    if (row.marketSide !== side) continue;
    if (row.price == null || !Number.isFinite(row.price) || row.price < 0) {
      continue;
    }
    return {
      price: row.price,
      currency: row.currency,
      sourceSystem: row.sourceSystem,
      sourceUrl: row.sourceUrl,
    };
  }
  return null;
}
