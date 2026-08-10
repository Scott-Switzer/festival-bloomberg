import test from 'node:test';
import assert from 'node:assert/strict';
import { correctResolutionStatus, resolveEntity } from '../../src/scraper/entity_resolution.js';

test('resolves aliases across canonical entity types', () => { const result = resolveEntity({ type: 'venue', name: 'The Fillmore' }, [{ canonicalId: 'v1', name: 'Fillmore', aliases: ['The Fillmore'], confidence: 1, provenance: [{ source: 'official', field: 'name' }] }]); assert.equal(result.status, 'resolved'); assert.equal(result.canonicalId, 'v1'); });
test('routes close candidates to ambiguity review', () => { const result = resolveEntity({ type: 'artist', name: 'Phoenix' }, [{ canonicalId: 'a1', name: 'Phoenix', confidence: 1, provenance: [] }, { canonicalId: 'a2', name: 'Phoenix', confidence: 1, provenance: [] }]); assert.equal(result.status, 'ambiguous'); assert.equal(result.reviewRequired, true); });
test('repairs stale statuses when a trusted source ID exists', () => { assert.equal(correctResolutionStatus('unresolved', [{ source: 'spotify', id: 'abc' }]), 'resolved'); assert.equal(correctResolutionStatus('unresolved', [{ source: 'other', id: 'abc' }]), 'unresolved'); });
