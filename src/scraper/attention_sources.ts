/**
 * Attention source resolution with Wikimedia primary + Spotify fallback foundation.
 *
 * Spotify requires credentials; when unavailable or unsuccessful the observation
 * is persisted as missing/error rather than inventing popularity values.
 */
import type { SpotifyArtist, SpotifyClient } from "./spotify";
import {
  INTELLIGENCE_METRIC_VERSION,
  WikimediaPageviewsClient,
  attentionObservationKey,
  type WikimediaPageviewsRequest,
} from "./wikimedia_pageviews";

export type AttentionSourceSystem = "wikimedia" | "spotify";

export type ArtistAttentionTarget = {
  artistKey: string;
  artistName: string;
  wikipediaTitle?: string;
  spotifyId?: string;
  festivalKey?: string;
  editionKey?: string;
  editionYear?: number;
};

export type AttentionObservationRow = {
  observation_key: string;
  artist_key: string;
  festival_key: string | null;
  edition_key: string | null;
  edition_year: number | null;
  source_system: AttentionSourceSystem;
  metric_kind: "pageviews" | "spotify_popularity" | "spotify_followers";
  project: string | null;
  access_method: string | null;
  agent: string | null;
  article_title: string | null;
  granularity: string | null;
  period_start: string | null;
  period_end: string | null;
  value: number | null;
  value_sum: number | null;
  value_unit: string | null;
  status: "ok" | "error" | "missing";
  error_code: string | null;
  error_message: string | null;
  source_url: string;
  retrieved_at: string;
  raw_response_json: unknown;
  provenance_json: unknown;
  metric_version: string;
};

export type AttentionResolveOptions = {
  pageviews: Omit<WikimediaPageviewsRequest, "articleTitle" | "artistKey">;
  /** Prefer Wikimedia; on missing/error optionally try Spotify. */
  enableSpotifyFallback?: boolean;
  /** Injected Spotify client. When absent, Spotify fallback records missing. */
  spotifyClient?: Pick<SpotifyClient, "getArtist" | "searchArtist"> | null;
};

export type AttentionResolveResult = {
  primary: AttentionObservationRow;
  fallback: AttentionObservationRow | null;
  /** Value preferred for cultural-velocity metrics (Wikimedia sum, else Spotify popularity). */
  culturalVelocity: number | null;
  culturalVelocitySource: AttentionSourceSystem | null;
};

export class AttentionSourceResolver {
  private readonly pageviews: WikimediaPageviewsClient;
  private readonly now: () => Date;

  constructor(opts: {
    pageviewsClient?: WikimediaPageviewsClient;
    now?: () => Date;
  } = {}) {
    this.pageviews = opts.pageviewsClient ?? new WikimediaPageviewsClient();
    this.now = opts.now ?? (() => new Date());
  }

  async resolveArtistAttention(
    target: ArtistAttentionTarget,
    options: AttentionResolveOptions,
  ): Promise<AttentionResolveResult> {
    const articleTitle = target.wikipediaTitle ?? target.artistName;
    const pageviewsResult = await this.pageviews.fetchPerArticle({
      ...options.pageviews,
      articleTitle,
      artistKey: target.artistKey,
      festivalKey: target.festivalKey,
      editionKey: target.editionKey,
      editionYear: target.editionYear,
    });

    const primary = this.pageviews.toAttentionObservation(pageviewsResult, {
      artistKey: target.artistKey,
      festivalKey: target.festivalKey,
      editionKey: target.editionKey,
      editionYear: target.editionYear,
    });

    let fallback: AttentionObservationRow | null = null;
    if (options.enableSpotifyFallback && primary.status !== "ok") {
      fallback = await this.fetchSpotifyFallback(target, options.spotifyClient);
    }

    if (primary.status === "ok" && primary.value != null) {
      return {
        primary,
        fallback,
        culturalVelocity: primary.value,
        culturalVelocitySource: "wikimedia",
      };
    }
    if (fallback?.status === "ok" && fallback.value != null) {
      return {
        primary,
        fallback,
        culturalVelocity: fallback.value,
        culturalVelocitySource: "spotify",
      };
    }
    return {
      primary,
      fallback,
      culturalVelocity: null,
      culturalVelocitySource: null,
    };
  }

  private async fetchSpotifyFallback(
    target: ArtistAttentionTarget,
    spotifyClient: AttentionResolveOptions["spotifyClient"],
  ): Promise<AttentionObservationRow> {
    const retrievedAt = this.now().toISOString();
    if (!spotifyClient) {
      return buildSpotifyAttentionObservation(target, null, {
        retrievedAt,
        status: "missing",
        errorCode: "spotify_client_unavailable",
        errorMessage:
          "Spotify fallback enabled but no client/credentials were provided",
      });
    }
    try {
      const artist = await fetchSpotifyArtist(target, spotifyClient);
      return buildSpotifyAttentionObservation(target, artist, { retrievedAt });
    } catch (error) {
      return buildSpotifyAttentionObservation(target, null, {
        retrievedAt,
        status: "error",
        errorCode: "spotify_request_failed",
        errorMessage: error instanceof Error ? error.message : String(error),
      });
    }
  }
}

async function fetchSpotifyArtist(
  target: ArtistAttentionTarget,
  client: Pick<SpotifyClient, "getArtist" | "searchArtist">,
): Promise<SpotifyArtist | null> {
  if (target.spotifyId) {
    return client.getArtist(target.spotifyId);
  }
  const matches = await client.searchArtist(target.artistName, 1);
  return matches[0] ?? null;
}

export function buildSpotifyAttentionObservation(
  target: ArtistAttentionTarget,
  artist: SpotifyArtist | null,
  opts: {
    retrievedAt?: string;
    errorCode?: string;
    errorMessage?: string;
    status?: "ok" | "error" | "missing";
  } = {},
): AttentionObservationRow {
  const retrievedAt = opts.retrievedAt ?? new Date().toISOString();
  const day = retrievedAt.slice(0, 10);
  const status =
    opts.status ?? (artist ? "ok" : opts.errorCode ? "error" : "missing");

  const observationKey = attentionObservationKey({
    artistKey: target.artistKey,
    sourceSystem: "spotify",
    metricKind: "spotify_popularity",
    project: null,
    periodStart: day,
    periodEnd: day,
    metricVersion: INTELLIGENCE_METRIC_VERSION,
  });

  return {
    observation_key: observationKey,
    artist_key: target.artistKey,
    festival_key: target.festivalKey ?? null,
    edition_key: target.editionKey ?? null,
    edition_year: target.editionYear ?? null,
    source_system: "spotify",
    metric_kind: "spotify_popularity",
    project: null,
    access_method: null,
    agent: null,
    article_title: null,
    granularity: "snapshot",
    period_start: day,
    period_end: day,
    value: artist ? artist.popularity : null,
    value_sum: artist ? artist.popularity : null,
    value_unit: "spotify_popularity_0_100",
    status,
    error_code: opts.errorCode ?? (artist ? null : "spotify_artist_not_found"),
    error_message:
      opts.errorMessage ??
      (artist ? null : "Spotify artist unavailable for attention fallback"),
    source_url: artist
      ? artist.external_urls.spotify
      : "https://api.spotify.com/v1/artists",
    retrieved_at: retrievedAt,
    raw_response_json: artist,
    provenance_json: {
      sourceSystem: "spotify",
      endpoint: target.spotifyId ? "artists.get" : "search",
      fallbackFor: "wikimedia_pageviews",
      spotifyId: artist?.id ?? target.spotifyId ?? null,
      followers: artist?.followers.total ?? null,
    },
    metric_version: INTELLIGENCE_METRIC_VERSION,
  };
}
