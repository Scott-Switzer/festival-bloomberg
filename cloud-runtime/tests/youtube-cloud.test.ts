import { describe, expect, it } from "vitest";
import { collectYouTubeBatch, MAX_BATCH } from "../src/youtube-cloud";

class MemoryBucket {
  data = new Map<string, string>();
  async get(key: string) { const value = this.data.get(key); return value == null ? null : { json: async () => JSON.parse(value), text: async () => value }; }
  async head(key: string) { return this.data.has(key) ? {} : null; }
  async put(key: string, value: string) { this.data.set(key, value); }
}

function env(payload: unknown) {
  const raw = new MemoryBucket();
  const lake = new MemoryBucket();
  const backup = new MemoryBucket();
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
  return { env: { RAW_BUCKET: raw, LAKE_BUCKET: lake, BACKUP_BUCKET: backup, YOUTUBE_API_KEY: "test", SOFTWARE_VERSION: "test" } as any, restore: () => { globalThis.fetch = original; }, raw, lake, backup };
}

describe("cloud YouTube collector", () => {
  it("uses batches of at most 50 and writes a value-change tick", async () => {
    expect(MAX_BATCH).toBe(50);
    const fixture = env({ items: [{ id: "UC1", statistics: { viewCount: "10", subscriberCount: "20", videoCount: "3", hiddenSubscriberCount: false } }] });
    try {
      const result = await collectYouTubeBatch(fixture.env, [{ artist_key: "a1", youtube_channel_id: "UC1" }], { now: new Date("2026-08-28T00:00:00Z") });
      expect(result.batches).toBe(1);
      expect(result.channels_resolved).toBe(1);
      expect(result.raw_objects).toBe(1);
      expect(result.value_changes).toBe(1);
      expect(result.heartbeats).toBe(0);
      expect(fixture.lake.data.size).toBe(1);
    } finally { fixture.restore(); }
  });

  it("writes a heartbeat without another raw object when the response is unchanged", async () => {
    const fixture = env({ items: [{ id: "UC1", statistics: { viewCount: "10", subscriberCount: "20", videoCount: "3", hiddenSubscriberCount: false } }] });
    try {
      const ids = [{ artist_key: "a1", youtube_channel_id: "UC1" }];
      const first = await collectYouTubeBatch(fixture.env, ids, { now: new Date("2026-08-28T00:00:00Z") });
      const second = await collectYouTubeBatch(fixture.env, ids, { now: new Date("2026-08-28T00:01:00Z") });
      expect(first.value_changes).toBe(1);
      expect(second.heartbeats).toBe(1);
      expect(second.raw_objects).toBe(0);
      expect(fixture.lake.data.size).toBe(2);
    } finally { fixture.restore(); }
  });
});
