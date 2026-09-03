/**
 * Bounded YouTube comments sentiment rail (official API only).
 *
 * For a small set of artists with verified channel IDs, fetches recent
 * comment threads from the channel's latest videos and writes ANONYMOUS
 * samples to staging/sentiment_samples/:
 *
 *   { artist_key, platform: "youtube", text, observed_at, engagement,
 *     language, video_id, rights_status, commercial_use_status }
 *
 * Contract (P10/P13):
 *   - NO usernames, user IDs, or profile URLs enter the sample.
 *   - Raw text is a public comment; the gold aggregator reduces it to daily
 *     aggregates and never exposes it in the product.
 *   - Official API first; bounded pilot only (maxArtists, maxCommentsPerVideo).
 *   - Quota: videos.list (1 unit) + commentThreads.list (1 unit per page).
 */

export interface SentimentPilotEnv {
  LAKE_BUCKET: R2Bucket;
  YOUTUBE_API_KEY: string;
  SOFTWARE_VERSION: string;
}

const YT = "https://www.googleapis.com/youtube/v3";

function intOrZero(v: string | undefined): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function collectYouTubeCommentSamples(
  env: SentimentPilotEnv,
  identities: Array<{ artist_key: string; youtube_channel_id: string }>,
  opts: { now?: Date; maxArtists?: number; maxCommentsPerVideo?: number; start?: number } = {},
): Promise<{
  status: string;
  artists_requested: number;
  artists_resolved: number;
  videos_checked: number;
  samples_written: number;
  quota_units_used: number;
  errors: number;
}> {
  const now = opts.now || new Date();
  // `start` allows bounded slices across the channel universe so scale runs
  // cover artists beyond the first N without resampling the same channels.
  const start = Math.max(0, opts.start ?? 0);
  const selected = identities.slice(start, start + (opts.maxArtists ?? 20));
  const maxComments = opts.maxCommentsPerVideo ?? 20;
  const out = {
    status: "COMPLETE", artists_requested: selected.length, artists_resolved: 0,
    videos_checked: 0, samples_written: 0, quota_units_used: 0, errors: 0,
  };
  if (!env.YOUTUBE_API_KEY) return { ...out, status: "BLOCKED_INVALID_KEY" };

  for (const identity of selected) {
    try {
      // 1. Latest video from the verified channel (uploads playlist).
      const uploadsUrl = `${YT}/channels?part=contentDetails&id=${encodeURIComponent(identity.youtube_channel_id)}&key=${encodeURIComponent(env.YOUTUBE_API_KEY)}`;
      out.quota_units_used++;
      const channelResp = await fetch(uploadsUrl);
      if (channelResp.status !== 200) { out.errors++; continue; }
      const channelData: any = await channelResp.json();
      const uploadsPlaylist = channelData.items?.[0]?.contentDetails?.relatedPlaylists?.uploads;
      if (!uploadsPlaylist) { out.errors++; continue; }

      const playlistUrl = `${YT}/playlistItems?part=contentDetails&playlistId=${encodeURIComponent(uploadsPlaylist)}&maxResults=3&key=${encodeURIComponent(env.YOUTUBE_API_KEY)}`;
      out.quota_units_used++;
      const playlistResp = await fetch(playlistUrl);
      if (playlistResp.status !== 200) { out.errors++; continue; }
      const playlistData: any = await playlistResp.json();
      const videoIds = (playlistData.items || [])
        .map((item: any) => item.contentDetails?.videoId)
        .filter(Boolean);
      if (!videoIds.length) { out.errors++; continue; }
      out.videos_checked += videoIds.length;
      out.artists_resolved++;

      // 2. Comment threads for the first video (bounded).
      const threadUrl = `${YT}/commentThreads?part=snippet&videoId=${encodeURIComponent(videoIds[0])}&maxResults=${Math.min(maxComments, 100)}&order=relevance&textFormat=plainText&key=${encodeURIComponent(env.YOUTUBE_API_KEY)}`;
      out.quota_units_used++;
      const threadResp = await fetch(threadUrl);
      if (threadResp.status !== 200) { out.errors++; continue; }
      const threadData: any = await threadResp.json();
      const items = threadData.items || [];

      const observedAt = now.toISOString();
      for (const item of items) {
        const snippet = item?.snippet?.topLevelComment?.snippet || item?.snippet || {};
        const text = (snippet.textDisplay || "").trim();
        if (!text) continue;
        const sample = {
          schema_version: "youtube_comment_sample_v1",
          artist_key: identity.artist_key,
          youtube_channel_id: identity.youtube_channel_id,
          platform: "youtube",
          text,
          // Engagement is aggregate only — never a per-user identity.
          engagement: intOrZero(snippet.likeCount) + 1,
          language: snippet.originalLanguage || snippet.audioLanguage || "unknown",
          observed_at: observedAt,
          retrieved_at: observedAt,
          knowledge_time: observedAt,
          source: "YOUTUBE_API",
          rights_status: "PROVIDER_TERMS_REVIEW_REQUIRED",
          commercial_use_status: "INTERNAL_ANALYTICS_ONLY",
          quota_units: 1,
        };
        const hash = await sha256Hex(JSON.stringify(sample));
        const key = `${observedAt.slice(0, 10)}/${identity.artist_key.replace(/[^A-Za-z0-9_-]/g, "_")}-${hash.slice(0, 12)}.json`;
        await env.LAKE_BUCKET.put(`staging/sentiment_samples/${key}`, JSON.stringify(sample), {
          httpMetadata: { contentType: "application/json" },
        });
        out.samples_written++;
        if (out.samples_written >= maxComments * selected.length) break;
      }
    } catch {
      out.errors++;
    }
  }
  return out;
}