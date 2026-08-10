import test from 'node:test';
import assert from 'node:assert/strict';
import { MusicBrainzClient, extractArtistData } from '../../src/scraper/musicbrainz.js';

test('requires descriptive MusicBrainz user agent', () => { assert.throws(() => new MusicBrainzClient('festival-bloomberg'), /User-Agent/); });
test('extracts aliases, country, and genres', () => { const data = extractArtistData({ id: '550e8400-e29b-41d4-a716-446655440000', name: 'Example', aliases: [{ name: 'Ex' }], country: 'US', tags: [{ count: 3, name: 'rock' }] }); assert.deepEqual(data.aliases[0].alias, 'Ex'); assert.equal(data.country, 'US'); assert.deepEqual(data.tags, ['rock']); });
