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

export interface CloudUniverse {
  version: string;
  events: CloudUniverseEvent[];
  youtube_channels?: Array<{ artist_key: string; youtube_channel_id: string; hot?: boolean; verified_at?: string }>;
  source?: string;
  updated_at?: string;
}

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

function taskFor(event: CloudUniverseEvent, family: PlannerFamily, now: Date): AcquisitionTask {
  const url = event.marketplace_event_url || event.canonical_url || "";
  const marketplace = event.marketplace || "ticketmaster.com";
  const rail: AcquisitionRail = family === "TICKET_STRUCTURED" ? "OTHER" : family === "TICKET_WEB" ? "FAST" : "OTHER";
  const provider: AcquisitionProvider = family === "TICKET_WEB" ? "monid" : "other";
  const window = now.toISOString().slice(0, 16);
  return {
    task_key: generateTaskKey(event.event_key, marketplace, rail, window, "v2"),
    event_key: event.event_key,
    provider_event_id: marketplace.includes("ticketmaster") ? (url.match(/event\/([^/?]+)/i)?.[1] || undefined) : undefined,
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
  const activeChannels: typeof channels = [];
  for (const channel of channels) {
    const state = await env.BACKUP_BUCKET.get(`control/youtube/state/${channel.youtube_channel_id}.json`);
    let status = "ACTIVE";
    if (state) { try { status = ((await state.json()) as any).status || "ACTIVE"; } catch {} }
    if (status !== "QUARANTINED") activeChannels.push(channel);
  }
  const hotChannels = activeChannels.filter((x) => x.hot);
  const isFullWindow = now.getUTCMinutes() % 15 === 0;
  const hot = Math.min(opts.youtube_hot_limit ?? 250, 250, hotChannels.length || Math.min(250, channels.length), Math.max(0, quota - used));
  const full = isFullWindow ? Math.min(opts.youtube_full_limit ?? activeChannels.length, activeChannels.length, Math.max(0, quota - used - hot)) : 0;
  const structured = exact.filter((e) => (e.marketplace || "").includes("ticketmaster") && e.marketplace_event_url).slice(0, opts.structured_limit ?? 100);
  const web = exact.filter((e) => !(e.marketplace || "").includes("ticketmaster") && e.marketplace_event_url).slice(0, opts.web_limit ?? 25);
  const youtubeCandidates = activeChannels.length;
  const families: FamilyPlan[] = [
    { family: "YOUTUBE_CHANNEL", candidate: youtubeCandidates, due: youtubeCandidates, selected: hot + full, deferred: Math.max(0, youtubeCandidates - hot - full), quota_blocked: Math.max(0, youtubeCandidates - (hot + full)), budget_blocked: 0 },
    { family: "YOUTUBE_VIDEO", candidate: 0, due: 0, selected: 0, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
    { family: "TICKET_STRUCTURED", candidate: structured.length, due: structured.length, selected: structured.length, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
    { family: "TICKET_WEB", candidate: web.length, due: web.length, selected: web.length, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
    { family: "ARTIST_DAILY", candidate: 0, due: 0, selected: 0, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
    { family: "MAINTENANCE", candidate: 1, due: 1, selected: 1, deferred: 0, quota_blocked: 0, budget_blocked: 0 },
  ];
  const tasks: Record<string, AcquisitionTask[]> = {
    YOUTUBE_CHANNEL: activeChannels.slice(0, hot + full).map((x) => ({
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
