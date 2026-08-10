/**
 * Versioned, conservative normalization primitives used by ingestion.
 *
 * Text is Unicode NFKC-normalized, zero-width spaces/BOMs are removed, and an
 * explicit Unicode whitespace set is collapsed. Case, punctuation, HTML, and
 * trailing URL slashes are intentionally preserved to avoid false matches.
 */
import { createHash } from "node:crypto";
import type { EvidenceCoordinate } from "./schemas";

export type JsonPrimitive = null | boolean | number | string;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export const NORMALIZATION_VERSION = "normalization-v1";
export const URL_POLICY_VERSION = "url-v1";
export const DEDUP_POLICY_VERSION = "dedup-v1";

const COLLAPSIBLE_WHITESPACE =
  /[\u0009-\u000D\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+/gu;
const REMOVABLE_ZERO_WIDTH = /[\u200B\uFEFF]/gu;

function compareStrings(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

/** Normalize human/source text without case-folding or stripping markup. */
export function normalizeText(value: string): string {
  return value
    .normalize("NFKC")
    .replace(REMOVABLE_ZERO_WIDTH, "")
    .replace(COLLAPSIBLE_WHITESPACE, " ")
    .trim();
}

/**
 * Convert a JSON-compatible value into a recursively normalized, key-sorted
 * representation. Unsupported/non-finite values fail rather than hashing an
 * ambiguous coercion.
 */
export function normalizeJson(value: unknown, path = "$"): JsonValue {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return normalizeText(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TypeError(`${path} must contain only finite JSON numbers`);
    }
    return Object.is(value, -0) ? 0 : value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => normalizeJson(item, `${path}[${index}]`));
  }
  if (typeof value === "object") {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError(`${path} must contain only plain JSON objects`);
    }
    const normalized: Record<string, JsonValue> = {};
    const normalizedKeys = new Set<string>();
    const entries = Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [normalizeText(key), item] as const)
      .sort(([left], [right]) => compareStrings(left, right));
    for (const [key, item] of entries) {
      if (normalizedKeys.has(key)) {
        throw new TypeError(`${path} contains colliding normalized key ${JSON.stringify(key)}`);
      }
      normalizedKeys.add(key);
      Object.defineProperty(normalized, key, {
        value: normalizeJson(item, `${path}.${key}`),
        enumerable: true,
        configurable: true,
        writable: true,
      });
    }
    return normalized;
  }
  throw new TypeError(`${path} must be JSON-compatible`);
}

/** Stable serialization for the normalized JSON subset accepted by ingestion. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(normalizeJson(value));
}

/** Normalize explicit deduplication text or derive it from a JSON payload. */
export function normalizedContent(payload: unknown, text?: string): string {
  if (text !== undefined) return normalizeText(text);
  if (typeof payload === "string") return normalizeText(payload);
  return canonicalJson(payload);
}

/** Domain-separated SHA-256 over a canonical JSON tuple. */
export function stableHash(namespace: string, ...parts: JsonValue[]): string {
  const preimage = canonicalJson([namespace, ...parts]);
  return createHash("sha256").update(preimage, "utf8").digest("hex");
}

/** Frozen list: only well-known marketing/click identifiers are removed. */
export const TRACKING_QUERY_PARAMETERS = new Set([
  "dclid",
  "fbclid",
  "gclid",
  "mc_cid",
  "mc_eid",
  "msclkid",
]);

function isTrackingParameter(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.startsWith("utm_") || TRACKING_QUERY_PARAMETERS.has(lower);
}

/**
 * Canonicalize a source URL with WHATWG URL semantics.
 *
 * Fragments and credentials do not identify fetched content. Known tracking
 * parameters are removed; all other query parameters and values are retained,
 * with keys sorted stably. Path casing and trailing slashes are preserved.
 */
export function canonicalizeUrl(value: string): string {
  const parsed = new URL(value.trim());
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new TypeError(`Unsupported source URL protocol: ${parsed.protocol}`);
  }
  parsed.protocol = parsed.protocol.toLowerCase();
  parsed.hostname = parsed.hostname.toLowerCase();
  parsed.username = "";
  parsed.password = "";
  parsed.hash = "";

  for (const [name] of [...parsed.searchParams.entries()]) {
    if (isTrackingParameter(name)) parsed.searchParams.delete(name);
  }
  parsed.searchParams.sort();
  return parsed.toString();
}

function evidenceIdentity(evidence: EvidenceCoordinate): string {
  return canonicalJson({
    url: evidence.url,
    selector: evidence.selector ?? null,
    xpath: evidence.xpath ?? null,
    jsonPath: evidence.jsonPath ?? null,
    charStart: evidence.charStart ?? null,
    charEnd: evidence.charEnd ?? null,
    snippet: evidence.snippet ?? null,
  });
}

/** Canonicalize, merge, and sort evidence while retaining earliest fetch time. */
export function mergeEvidence(
  ...groups: ReadonlyArray<readonly EvidenceCoordinate[]>
): EvidenceCoordinate[] {
  const byIdentity = new Map<string, EvidenceCoordinate>();
  for (const group of groups) {
    for (const evidence of group) {
      const normalized: EvidenceCoordinate = {
        url: canonicalizeUrl(evidence.url),
        fetchedAt: new Date(evidence.fetchedAt).toISOString(),
        ...(evidence.selector === undefined
          ? {}
          : { selector: normalizeText(evidence.selector) }),
        ...(evidence.xpath === undefined ? {} : { xpath: normalizeText(evidence.xpath) }),
        ...(evidence.jsonPath === undefined
          ? {}
          : { jsonPath: normalizeText(evidence.jsonPath) }),
        ...(evidence.charStart === undefined ? {} : { charStart: evidence.charStart }),
        ...(evidence.charEnd === undefined ? {} : { charEnd: evidence.charEnd }),
        ...(evidence.snippet === undefined
          ? {}
          : { snippet: normalizeText(evidence.snippet) }),
      };
      const identity = evidenceIdentity(normalized);
      const current = byIdentity.get(identity);
      if (!current || normalized.fetchedAt < current.fetchedAt) {
        byIdentity.set(identity, normalized);
      }
    }
  }
  return [...byIdentity.entries()]
    .sort(([left], [right]) => compareStrings(left, right))
    .map(([, evidence]) => evidence);
}
