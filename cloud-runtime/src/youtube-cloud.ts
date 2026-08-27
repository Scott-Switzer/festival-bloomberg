/** Cloud-native YouTube channels.list tape. No channel discovery occurs here. */

export interface YouTubeChannelIdentity {
  artist_key: string;
  youtube_channel_id: string;
}

export interface YouTubeCloudEnv {
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  YOUTUBE_API_KEY: string;
  SOFTWARE_VERSION: string;
}

export type ChannelHealthStatus = "ACTIVE" | "MISSING" | "TRANSIENT_ERROR" | "QUARANTINED" | "REVERIFY_DUE";

interface ChannelState {
  value_hash?: string;
  values?: Record<string, unknown>;
  raw_evidence_ref?: string;
  observed_at?: string;
  status?: ChannelHealthStatus;
  consecutive_failures?: number;
  last_success_at?: string;
  last_failure_at?: string;
  last_checked_at?: string;
}

interface YouTubeApiItem {
  id: string;
  statistics?: {
    viewCount?: string;
    subscriberCount?: string;
    hiddenSubscriberCount?: boolean;
    videoCount?: string;
  };
}

const API = "https://www.googleapis.com/youtube/v3/channels";
const MAX_BATCH = 50;
const STATE_PREFIX = "control/youtube/state/";

function jsonHash(value: unknown): string {
  const text = JSON.stringify(value);
  let h1 = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h1 ^= text.charCodeAt(i);
    h1 = Math.imul(h1, 0x01000193);
  }
  return (h1 >>> 0).toString(16).padStart(8, "0");
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function intOrNull(v: string | undefined): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function trackedValues(item: YouTubeApiItem): Record<string, unknown> {
  const s = item.statistics || {};
  return {
    subscriber_count: intOrNull(s.subscriberCount),
    subscriber_count_hidden: s.hiddenSubscriberCount === true,
    subscriber_precision: s.hiddenSubscriberCount === true ? "HIDDEN" : "EXACT_AS_EXPOSED",
    channel_view_count: intOrNull(s.viewCount),
    video_count: intOrNull(s.videoCount),
  };
}

function changedFields(previous: Record<string, unknown> | undefined, current: Record<string, unknown>): string[] {
  if (!previous) return Object.keys(current);
  return Object.keys(current).filter((key) => previous[key] !== current[key]);
}

async function getState(env: YouTubeCloudEnv, channelId: string): Promise<ChannelState | null> {
  const obj = await env.BACKUP_BUCKET.get(`${STATE_PREFIX}${channelId}.json`);
  if (!obj) return null;
  try { return await obj.json() as ChannelState; } catch { return null; }
}

async function putState(env: YouTubeCloudEnv, channelId: string, state: ChannelState): Promise<void> {
  await env.BACKUP_BUCKET.put(`${STATE_PREFIX}${channelId}.json`, JSON.stringify(state), {
    httpMetadata: { contentType: "application/json" },
  });
}

async function fetchBatch(ids: string[], apiKey: string): Promise<{ status: number; payload: any; raw: string }> {
  const url = `${API}?part=statistics&id=${encodeURIComponent(ids.join(","))}&maxResults=${ids.length}&key=${encodeURIComponent(apiKey)}`;
  const response = await fetch(url);
  const raw = await response.text();
  let payload: any = {};
  try { payload = JSON.parse(raw); } catch { /* reported below */ }
  return { status: response.status, payload, raw };
}

export async function collectYouTubeBatch(
  env: YouTubeCloudEnv,
  identities: YouTubeChannelIdentity[],
  opts: { now?: Date; maxChannels?: number } = {},
): Promise<{
  status: string;
  batches: number;
  channels_requested: number;
  channels_resolved: number;
  channels_missing: number;
  checks: number;
  raw_objects: number;
  normalized_ticks: number;
  value_changes: number;
  heartbeats: number;
  quota_units_used: number;
  errors: number;
}> {
  const now = opts.now || new Date();
  const selected = identities.slice(0, opts.maxChannels ?? identities.length);
  const out = {
    status: "COMPLETE", batches: 0, channels_requested: selected.length,
    channels_resolved: 0, channels_missing: 0, checks: 0, raw_objects: 0,
    normalized_ticks: 0, value_changes: 0, heartbeats: 0, quota_units_used: 0,
    errors: 0,
  };
  if (!env.YOUTUBE_API_KEY) return { ...out, status: "BLOCKED_INVALID_KEY" };

  for (let offset = 0; offset < selected.length; offset += MAX_BATCH) {
    const batch = selected.slice(offset, offset + MAX_BATCH);
    out.batches++;
    out.quota_units_used++;
    const fetched = await fetchBatch(batch.map((x) => x.youtube_channel_id), env.YOUTUBE_API_KEY);
    if (fetched.status !== 200) {
      out.errors++;
      continue;
    }
    const hash = await sha256Hex(fetched.raw);
    const rawKey = `raw/youtube/${hash.slice(0, 2)}/${hash.slice(2, 4)}/${hash}.json`;
    const existingRaw = await env.RAW_BUCKET.head(rawKey);
    if (!existingRaw) {
      await env.RAW_BUCKET.put(rawKey, fetched.raw, {
        httpMetadata: { contentType: "application/json" },
        customMetadata: { source: "YOUTUBE_API", content_hash: hash },
      });
      out.raw_objects++;
    }
    const byId = new Map<string, YouTubeApiItem>((fetched.payload.items || []).map((x: YouTubeApiItem) => [x.id, x]));
    for (const identity of batch) {
      out.checks++;
      const item = byId.get(identity.youtube_channel_id);
      const previous = await getState(env, identity.youtube_channel_id);
      if (!item) {
        out.channels_missing++;
        const failures = (previous?.consecutive_failures || 0) + 1;
        const status: ChannelHealthStatus = failures >= 3 ? "QUARANTINED" : "MISSING";
        await putState(env, identity.youtube_channel_id, { ...previous, status, consecutive_failures: failures, last_failure_at: now.toISOString(), last_checked_at: now.toISOString() });
        continue;
      }
      out.channels_resolved++;
      const values = trackedValues(item);
      const valueHash = jsonHash(values);
      const fields = changedFields(previous?.values, values);
      const tickType = previous && fields.length === 0 ? "HEARTBEAT" : "VALUE_CHANGE";
      const observedAt = now.toISOString();
      const tick = {
        schema_version: "youtube_channel_tick_v1",
        tick_type: tickType,
        artist_key: identity.artist_key,
        youtube_channel_id: identity.youtube_channel_id,
        observed_at: observedAt,
        retrieved_at: observedAt,
        knowledge_time: observedAt,
        ...values,
        changed_fields: fields,
        previous_value_hash: previous?.value_hash || null,
        current_value_hash: valueHash,
        raw_evidence_ref: `r2://${rawKey}`,
        source: "YOUTUBE_API",
        quota_units: 1,
        rights_status: "PROVIDER_TERMS_REVIEW_REQUIRED",
        commercial_use_status: "INTERNAL_ANALYTICS_ONLY",
      };
      const tickKey = `staging/youtube/date=${observedAt.slice(0, 10)}/hour=${observedAt.slice(11, 13)}/minute=${observedAt.slice(14, 16).replace(":", "-")}/${identity.artist_key.replace(/[^A-Za-z0-9_-]/g, "_")}-${valueHash}-${observedAt.replace(/[^0-9]/g, "").slice(0, 14)}.json`;
      await env.LAKE_BUCKET.put(tickKey, JSON.stringify(tick), { httpMetadata: { contentType: "application/json" } });
      await putState(env, identity.youtube_channel_id, { value_hash: valueHash, values, raw_evidence_ref: `r2://${rawKey}`, observed_at: observedAt, status: "ACTIVE", consecutive_failures: 0, last_success_at: observedAt, last_checked_at: observedAt });
      out.normalized_ticks++;
      if (tickType === "HEARTBEAT") out.heartbeats++; else out.value_changes++;
    }
  }
  return out;
}

export { MAX_BATCH };
