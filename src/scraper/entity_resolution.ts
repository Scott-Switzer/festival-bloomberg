/** Canonical identity resolution for artists, festivals, venues, and events. */
export type EntityType = 'artist' | 'festival' | 'venue' | 'event';
export type ResolutionStatus = 'resolved' | 'ambiguous' | 'unresolved';
export type SourceId = { source: string; id: string };
export type Provenance = { source: string; field: string; observedAt?: string };

export interface EntityCandidate {
  canonicalId: string;
  name: string;
  aliases?: string[];
  sourceIds?: SourceId[];
  confidence: number;
  provenance: Provenance[];
}
export interface EntityInput {
  type: EntityType;
  name: string;
  aliases?: string[];
  sourceIds?: SourceId[];
  location?: string;
  date?: string;
}
export interface ResolutionResult {
  status: ResolutionStatus;
  canonicalId: string | null;
  confidence: number;
  candidates: EntityCandidate[];
  provenance: Provenance[];
  reviewRequired: boolean;
}

export function normalizeEntityName(value: string): string {
  return value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
}
function overlap(a: string[], b: string[]): number {
  const left = new Set(a.map(normalizeEntityName)); const right = new Set(b.map(normalizeEntityName));
  return left.size && [...left].filter(x => right.has(x)).length / Math.max(left.size, right.size) || 0;
}
function score(input: EntityInput, candidate: EntityCandidate): number {
  const names = [input.name, ...(input.aliases ?? [])];
  const candidateNames = [candidate.name, ...(candidate.aliases ?? [])];
  const exact = names.some(n => candidateNames.some(c => normalizeEntityName(n) === normalizeEntityName(c)));
  const aliasScore = overlap(names, candidateNames);
  const idScore = (input.sourceIds ?? []).some(i => (candidate.sourceIds ?? []).some(c => i.source === c.source && i.id === c.id)) ? 1 : 0;
  return Math.min(1, idScore ? 1 : exact ? 0.96 : 0.75 * aliasScore);
}

export function resolveEntity(input: EntityInput, candidates: EntityCandidate[], options: { autoResolveAt?: number; ambiguityMargin?: number } = {}): ResolutionResult {
  const autoResolveAt = options.autoResolveAt ?? 0.9;
  const ambiguityMargin = options.ambiguityMargin ?? 0.08;
  const ranked = candidates.map(candidate => ({ candidate, confidence: score(input, candidate) }))
    .sort((a, b) => b.confidence - a.confidence);
  const best = ranked[0]; const next = ranked[1];
  if (!best || best.confidence === 0) return { status: 'unresolved', canonicalId: null, confidence: 0, candidates, provenance: [], reviewRequired: false };
  const ambiguous = best.confidence < autoResolveAt || !!next && best.confidence - next.confidence < ambiguityMargin;
  const provenance = best.candidate.provenance;
  return { status: ambiguous ? 'ambiguous' : 'resolved', canonicalId: ambiguous ? null : best.candidate.canonicalId, confidence: best.confidence, candidates: ranked.map(x => x.candidate), provenance, reviewRequired: ambiguous };
}

/** Existing IDs are authoritative enough to repair stale unresolved statuses. */
export function correctResolutionStatus(status: ResolutionStatus, sourceIds: SourceId[]): ResolutionStatus {
  return status === 'unresolved' && sourceIds.some(x => ['musicbrainz', 'spotify'].includes(x.source.toLowerCase()) && x.id.trim()) ? 'resolved' : status;
}
