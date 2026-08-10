import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MusicBrainzClient,
  DEFAULT_MUSICBRAINZ_USER_AGENT,
  extractArtistData,
  getMusicBrainzClient,
} from '../../src/scraper/musicbrainz.js';

test('requires descriptive MusicBrainz user agent', () => {
  assert.throws(() => new MusicBrainzClient('festival-bloomberg'), /User-Agent/);
});

test('rejects example.com placeholder user agent', () => {
  assert.throws(
    () =>
      new MusicBrainzClient(
        'FestivalBloomberg/1.0 (maintainer: scott.switzer@example.com)',
      ),
    /example\.com/,
  );
});

test('default user agent uses maintainer email, not example.com', () => {
  assert.match(DEFAULT_MUSICBRAINZ_USER_AGENT, /scott\.t\.switzer@gmail\.com/);
  assert.doesNotMatch(DEFAULT_MUSICBRAINZ_USER_AGENT, /example\.com/);
  const client = getMusicBrainzClient();
  assert.ok(client instanceof MusicBrainzClient);
});

test('extracts aliases, country, and genres', () => {
  const data = extractArtistData({
    id: '550e8400-e29b-41d4-a716-446655440000',
    name: 'Example',
    aliases: [{ name: 'Ex' }],
    country: 'US',
    tags: [{ count: 3, name: 'rock' }],
  });
  assert.deepEqual(data.aliases[0].alias, 'Ex');
  assert.equal(data.country, 'US');
  assert.deepEqual(data.tags, ['rock']);
});

test('serializes concurrent request start reservations at least rateLimitDelay apart', async () => {
  const starts: number[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    starts.push(Date.now());
    return new Response(JSON.stringify({ tags: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;

  try {
    const client = new MusicBrainzClient(
      'FestivalBloomberg/1.0 (scott.t.switzer@gmail.com)',
      50,
    );
    await Promise.all([
      client.getArtistTags('550e8400-e29b-41d4-a716-446655440000'),
      client.getArtistTags('550e8400-e29b-41d4-a716-446655440001'),
      client.getArtistTags('550e8400-e29b-41d4-a716-446655440002'),
    ]);
    assert.equal(starts.length, 3);
    const gaps = [starts[1]! - starts[0]!, starts[2]! - starts[1]!];
    for (const gap of gaps) {
      // Allow a few ms of timer jitter; must still be near the configured delay.
      assert.ok(gap >= 40, `expected gap >= 40ms, got ${gap}`);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getArtistReleases queries arid and returns releases array', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return new Response(
      JSON.stringify({
        releases: [{ id: 'r1', title: 'Album' }],
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }) as typeof fetch;

  try {
    const client = new MusicBrainzClient(
      'FestivalBloomberg/1.0 (contact=festival-bloomberg)',
      0,
    );
    const releases = await client.getArtistReleases(
      '550e8400-e29b-41d4-a716-446655440000',
      'album',
    );
    assert.equal(releases.length, 1);
    assert.equal(releases[0]?.title, 'Album');
    assert.match(calls[0] ?? '', /release\?/);
    assert.match(calls[0] ?? '', /arid%3A550e8400/);
    assert.match(calls[0] ?? '', /type=album/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
