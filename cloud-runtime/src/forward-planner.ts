import { AcquisitionTask, AcquisitionRail, AcquisitionProvider, generateTaskKey } from "./task-contract";

export type PlannerFamily = "YOUTUBE_CHANNEL" | "YOUTUBE_VIDEO" | "TICKET_STRUCTURED" | "TICKET_WEB" | "ARTIST_DAILY" | "MAINTENANCE";

export interface FamilyPlan {
  family: PlannerFamily;
  candidate: number;
  due: number;
  selected: number;
  deferred: number;
  quota_blocked: number;
  budget_blocked: number;
}

export interface ForwardPlannerEnv {
  BACKUP_BUCKET: R2Bucket;
  YOUTUBE_QUEUE: Queue;
  STRUCTURED_API_QUEUE: Queue;
  BROWSER_QUEUE: Queue;
  MONID_QUEUE: Queue;
  SOFTWARE_VERSION: string;
  YOUTUBE_DAILY_QUOTA?: string;
}

export interface CloudUniverseEvent {
  event_key: string;
  provider_event_id?: string;
  artist_key?: string;
  artist_name?: string;
  event_date?: string;
  venue_name?: string;
  city?: string;
  marketplace?: string;
  marketplace_event_url?: string;
  canonical_url?: string;
  mapping_status?: string;
  acquisition_tier?: "HOT_EVENTS" | "ACTIVE_EVENTS" | "LONG_HORIZON_EVENTS";
}

/** Scheduler-facing state copied into the compact active-channel artifact. */
type ChannelScheduleState = "ACTIVE" | "MISSING" | "TRANSIENT_ERROR" | "QUARANTINED" | "REVERIFY_DUE";

interface YouTubeChannelScheduleEntry {
  artist_key: string;
  youtube_channel_id: string;
  hot?: boolean;
  verified_at?: string;
  status?: ChannelScheduleState;
}

export interface CloudUniverse {
  version: string;
  events: CloudUniverseEvent[];
  youtube_channels?: YouTubeChannelScheduleEntry[];
  source?: string;
  updated_at?: string;
}

// Ticket families have explicit dispatch windows so the one-minute master
// clock cannot turn a bounded pilot into an unbounded queue/budget drain.
const STRUCTURED_DISPATCH_MINUTES = 15;
const WEB_DISPATCH_INTERVAL_HOURS = 6;

export async function loadV2Universe(env: Pick<ForwardPlannerEnv, "BACKUP_BUCKET">): Promise<CloudUniverse> {
  const youtubeObj = await env.BACKUP_BUCKET.get("control/youtube/active_channels.json");
  const pointerObj = await env.BACKUP_BUCKET.get("control/watch_universe/current.json");
  const youtubeChannels = youtubeObj ? ((await youtubeObj.json() as any).channels || []) : [];
  if (!pointerObj) return { version: "missing", events: [], youtube_channels: youtubeChannels };
  const pointer = await pointerObj.json() as { source?: string; version?: string };
  const key = pointer.source || "control/watch_universe/v2/current.json";
  let object = await env.BACKUP_BUCKET.get(key);
  if (!object && key !== "control/watch_universe/v2/current.json") {
    object = await env.BACKUP_BUCKET.get("control/watch_universe/v2/current.json");
  }
  if (!object) return { version: pointer.version || "missing", events: [], youtube_channels: youtubeChannels };
  const data = await object.json() as Partial<CloudUniverse>;
  return { version: data.version || pointer.version || "v2", events: data.events || [], youtube_channels: data.youtube_channels || youtubeChannels, source: key, updated_at: data.updated_at };
}

/**
 * Return the provider-native Ticketmaster event id from the canonical record
 * or its URL.  URL parsing is deliberately pathname-based so query strings,
 * fragments, encoded ids, and additional path segments cannot corrupt it.
 */
export function extractTicketmasterEventId(event: Pick<CloudUniverseEvent, "provider_event_id" | "marketplace_event_url" | "canonical_url">): string | undefined {
  const nativeId = event.provider_event_id?.trim();
  if (nativeId) return nativeId;
  const url = event.marketplace_event_url || event.canonical_url || "";
  const matchPath = (pathname: string): string | undefined => {
    const match = pathname.match(/(?:^|\/)event\/([^/?#]+)/i);
    if (!match?.[1]) return undefined;
    try { return decodeURIComponent(match[1]); } catch { return match[1]; }
  };
  try { return matchPath(new URL(url).pathname); } catch { return matchPath(url); }
}

export function taskFor(event: CloudUniverseEvent, family: PlannerFamily, now: Date): AcquisitionTask {
  const url = event.marketplace_event_url || event.canonical_url || "";
  const marketplace = event.marketplace || "ticketmaster.com";
  const rail: AcquisitionRail = family === "TICKET_STRUCTURED" ? "OTHER" : family === "TICKET_WEB" ? "FAST" : "OTHER";
  const provider: AcquisitionProvider = family === "TICKET_WEB" ? "monid" : "other";
  const window = now.toISOString().slice(0, 16);
  return {
    task_key: generateTaskKey(event.event_key, marketplace, rail, window, "v2"),
    event_key: event.event_key,
    // Preserve an already mapped native id for every rail.  For legacy rows,
    // recover it from the Ticketmaster URL only when this is a TM task.
    provider_event_id: event.provider_event_id || (marketplace.toLowerCase().includes("ticketmaster") ? extractTicketmasterEventId(event) : undefined),
    acquisition_provider: provider,
    marketplace: marketplace as any,
    rail,
    target_url: url,
    scheduled_window: window,
    priority: event.acquisition_tier === "HOT_EVENTS" ? 1 : event.acquisition_tier === "ACTIVE_EVENTS" ? 2 : 3,
    expected_max_cost_usd: family === "TICKET_WEB" ? 0.0009 : 0,
    created_at: now.toISOString(),
    software_version: "cloud-acquisition-runtime-v2",
    mapping_version: "v2",
    event_metadata: { artist_name: event.artist_name, venue_name: event.venue_name, city: event.city, event_date: event.event_date },
    trigger: "SCHEDULED",
    run_id: `cron_${now.toISOString().slice(0, 16)}`,
  };
}

function rotatedTake<T>(items: T[], offset: number, limit: number): T[] {
  if (!items.length || limit <= 0) return [];
  const count = Math.min(items.length, limit);
  return Array.from({ length: count }, (_, index) => items[(offset + index) % items.length]);
}

export async function planForwardFamilies(
  env: ForwardPlannerEnv,
  opts: { now?: Date; youtube_hot_limit?: number; youtube_full_limit?: number; structured_limit?: number; web_limit?: number; youtube_quota_used_today?: number } = {},
): Promise<{ families: FamilyPlan[]; tasks: Record<string, AcquisitionTask[]>; watch_universe_size: number }> {
  const now = opts.now || new Date();
  const universe = await loadV2Universe(env);
  const future = universe.events.filter((e) => e.event_key && (!e.event_date || new Date(e.event_date).getTime() >= now.getTime()));
  const exact = future.filter((e) => ["EXACT_PROVIDER_ID", "EXACT_PAGE_MATCH", "HIGH_CONFIDENCE"].includes(e.mapping_status || ""));
  const quota = Number(env.YOUTUBE_DAILY_QUOTA || "9000");
  const used = opts.youtube_quota_used_today || 0;
  const channels = (universe.youtube_channels || []).filter((x) => x.artist_key && x.youtube_channel_id);
  // The active-channel artifact is the scheduler's bounded, read-optimized
  // snapshot.  Fetching one R2 state object per channel here made each minute
  // tick issue 13k+ reads for the current universe and exhaust the Worker
  // invocation resource budget before queue dispatch.  Consumers still write
  // detailed per-channel state; the next explicit promotion folds quarantines
  // into this snapshot for scheduling.
  const activeChannels = channels.filter((channel) => channel.status !== "QUARANTINED");
  const hotChannels = activeChannels.filter((x) => x.hot);
  const isHourlyWindow = now.getUTCMinutes() === 0;
  const isDailyWindow = now.getUTCHours() === 0 && now.getUTCMinutes() === 0;
  const isStructuredWindow = now.getUTCMinutes() % STRUCTURED_DISPATCH_MINUTES === 0;
  const isWebWindow = isStructuredWindow && now.getUTCHours() % WEB_DISPATCH_INTERVAL_HOURS === 0;
  // Deterministic windows bound quota without requiring mutable scheduler
  // state: hot channels run hourly, while cold channels run once/day in a
  // UTC-day rotation.  The 24 hourly hot allocations plus the daily cold
  // allocation are never greater than YOUTUBE_DAILY_QUOTA.
  const hotPool = hotChannels.length ? hotChannels : activeChannels;
  const hotLimit = Math.min(opts.youtube_hot_limit ?? 250, 250, hotPool.length, Math.floor(quota / 24));
  const hotSelected = isHourlyWindow ? hotPool.slice(0, Math.min(hotLimit, Math.max(0, quota - used))) : [];
  const hotKeys = new Set(hotSelected.map((channel) => channel.youtube_channel_id));
  const coldChannels = activeChannels.filter((channel) => !hotKeys.has(channel.youtube_channel_id));
  const hotDailyQuota = hotLimit * 24;
  const coldDailyQuota = Math.max(0, quota - used - hotDailyQuota);
  const dayIndex = Math.floor(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) / 86_400_000);
  const full = isDailyWindow
    ? rotatedTake(coldChannels, (dayIndex * Math.max(1, coldDailyQuota)) % Math.max(1, coldChannels.length), Math.min(opts.youtube_full_limit ?? coldDailyQuota, coldChannels.length, coldDailyQuota))
    : [];
  const hot = hotSelected.length;
  // Structured API tasks require a native provider identity.  URL-only or
  // malformed rows stay visible as candidates only after they are repaired;
  // they must never become guaranteed retry traffic in the API consumer.
  const structuredCandidates = exact.filter((e) => (e.marketplace || "").toLowerCase().includes("ticketmaster") && Boolean(extractTicketmasterEventId(e)));
  const webCandidates = exact.filter((e) => !(e.marketplace || "").includes("ticketmaster") && e.marketplace_event_url);
  const structuredLimit = Math.min(opts.structured_limit ?? 25, 25);
  const structuredWindow = Math.floor(now.getTime() / (STRUCTURED_DISPATCH_MINUTES * 60 * 1000));
  const structured = isStructuredWindow
    ? rotatedTake(structuredCandidates, (structuredWindow * Math.max(1, structuredLimit)) % Math.max(1, structuredCandidates.length), structuredLimit)
    : [];
  // Four 25-task windows/day at the measured $0.0009 Monid unit cost is
  // $0.09/day, below the $0.25 automated daily budget.  A persisted Governor
  // budget check still belongs in the consumer for authoritative admission.
  const web = isWebWindow ? webCandidates.slice(0, opts.web_limit ?? 25) : [];
  const youtubeCandidates = activeChannels.length;
  const families: FamilyPlan[] = [
    { family: "YOUTUBE_CHANNEL", candidate: youtubeCandidates, due: youtubeCandidates, selected: hot + full.length, deferred: Math.max(0, youtubeCandidates - hot - full.length), quota_blocked: Math.max(0, youtubeCandidates - (hot + full.length)), budget_blocked: 0 },
    { family: "YOUTUBE_VIDEO", candidate: 0, due: 0, selected: 0, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
    { family: "TICKET_STRUCTURED", candidate: structuredCandidates.length, due: structuredCandidates.length, selected: structured.length, deferred: Math.max(0, structuredCandidates.length - structured.length), quota_blocked: 0, budget_blocked: 0 },
    { family: "TICKET_WEB", candidate: webCandidates.length, due: webCandidates.length, selected: web.length, deferred: Math.max(0, webCandidates.length - web.length), quota_blocked: 0, budget_blocked: 0 },
    { family: "ARTIST_DAILY", candidate: 0, due: 0, selected: 0, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
    { family: "MAINTENANCE", candidate: 1, due: 1, selected: 1, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
  ];
  const tasks: Record<string, AcquisitionTask[]> = {
    YOUTUBE_CHANNEL: [...hotSelected, ...full].map((x) => ({
      task_key: `youtube_${x.youtube_channel_id}_${now.toISOString().slice(0, 16).replace(/[:-]/g, "")}`,
      event_key: x.artist_key,
      acquisition_provider: "other",
      marketplace: "unknown",
      rail: "OTHER",
      target_url: x.youtube_channel_id,
      scheduled_window: now.toISOString().slice(0, 16),
      priority: x.hot ? 1 : 2,
      expected_max_cost_usd: 0,
      created_at: now.toISOString(),
      software_version: "cloud-acquisition-runtime-v2",
      mapping_version: "v2",
      trigger: "SCHEDULED",
      run_id: `cron_${now.toISOString().slice(0, 16)}`,
    } as AcquisitionTask)),
    YOUTUBE_VIDEO: [],
    TICKET_STRUCTURED: structured.map((e) => taskFor(e, "TICKET_STRUCTURED", now)),
    TICKET_WEB: web.map((e) => taskFor(e, "TICKET_WEB", now)),
    ARTIST_DAILY: [],
    MAINTENANCE: [],
  };
  return { families, tasks, watch_universe_size: universe.events.length };
}
