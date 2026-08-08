/**
 * HTML parsing for JSON-LD, meta, and semantic tags.
 * Lightweight OCR text/layout parsing with optional NVIDIA NIM extraction.
 */

import { createHash } from "node:crypto";
import { readFile, mkdir, rename, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { z } from "zod";

export type JsonLdNode = Record<string, unknown>;

export type MetaTags = {
  title?: string;
  description?: string;
  og: Record<string, string>;
  twitter: Record<string, string>;
  other: Record<string, string>;
};

export type SemanticArtistHint = {
  name: string;
  source: "jsonld" | "semantic" | "meta";
  url?: string;
};

export type ParsedPage = {
  jsonLd: JsonLdNode[];
  meta: MetaTags;
  artists: SemanticArtistHint[];
  headings: string[];
  /** Reserved for future OCR / poster layout pipelines. */
  ocr?: OcrParseResult;
  posterLayout?: PosterLayoutResult;
};

export type OcrParseResult = {
  textBlocks: Array<{ text: string; confidence?: number; bbox?: BBox }>;
  engine?: string;
};

export type LayoutArtist = {
  name: string;
  tier?: string;
  sourceBlockIds: string[];
  bbox?: BBox;
};

export type LayoutTier = {
  name: string;
  rank: number;
  artists: string[];
  sourceBlockIds: string[];
  bbox?: BBox;
};

export type ScheduleDetail = {
  artist?: string;
  date?: string;
  time?: string;
  stage?: string;
  venue?: string;
  sourceBlockIds: string[];
  bbox?: BBox;
};

export type PosterLayoutResult = {
  regions: Array<{ role: "headline" | "artist" | "date" | "venue" | "other"; bbox: BBox; text?: string }>;
  artists?: LayoutArtist[];
  tiers?: LayoutTier[];
  schedule?: ScheduleDetail[];
  engine?: string;
  source?: "local" | "nim" | "hook";
};

export type BBox = { x: number; y: number; w: number; h: number };

export type OcrHook = (image: Uint8Array | string) => Promise<OcrParseResult>;
export type PosterLayoutHook = (image: Uint8Array | string) => Promise<PosterLayoutResult>;

export type ParserHooks = {
  ocr?: OcrHook;
  posterLayout?: PosterLayoutHook;
};

export type NimClientOptions = {
  baseUrl?: string;
  endpoint?: string;
  model?: string;
  apiKey?: string;
  maxInputTokens?: number;
  maxOutputTokens?: number;
  timeoutMs?: number;
  fetch?: typeof fetch;
};

export type LayoutAnalysisOptions = {
  cacheDir?: string;
  no_paid_requests?: boolean;
  nim?: NimClientOptions;
  /** Reuse an already parsed OCR result when analyzing the same input. */
  ocr?: OcrParseResult;
};

const BBOX_SCHEMA = z.object({
  x: z.number().finite().nonnegative(),
  y: z.number().finite().nonnegative(),
  w: z.number().finite().positive(),
  h: z.number().finite().positive(),
}).strict();

const OCR_SCHEMA = z.object({
  textBlocks: z.array(z.object({
    text: z.string().min(1).max(2_000),
    confidence: z.number().min(0).max(1).optional(),
    bbox: BBOX_SCHEMA.optional(),
  }).strict()).max(500),
  engine: z.string().optional(),
}).strict();

const SOURCE_IDS = z.array(z.string().regex(/^b\d+$/)).min(1).max(100);
const NIM_EXTRACTION_SCHEMA = z.object({
  artists: z.array(z.object({
    name: z.string().min(1).max(200),
    tier: z.string().min(1).max(100).optional(),
    sourceBlockIds: SOURCE_IDS,
  }).strict()).max(200),
  tiers: z.array(z.object({
    name: z.string().min(1).max(100),
    rank: z.number().int().positive().max(100),
    artists: z.array(z.string().min(1).max(200)).max(200),
    sourceBlockIds: SOURCE_IDS,
  }).strict()).max(100),
  schedule: z.array(z.object({
    artist: z.string().min(1).max(200).optional(),
    date: z.string().min(1).max(100).optional(),
    time: z.string().min(1).max(100).optional(),
    stage: z.string().min(1).max(100).optional(),
    venue: z.string().min(1).max(200).optional(),
    sourceBlockIds: SOURCE_IDS,
  }).strict()).max(300),
}).strict();

const POSTER_SCHEMA = z.object({
  regions: z.array(z.object({
    role: z.enum(["headline", "artist", "date", "venue", "other"]),
    bbox: BBOX_SCHEMA,
    text: z.string().optional(),
  }).strict()),
  artists: z.array(z.object({
    name: z.string(),
    tier: z.string().optional(),
    sourceBlockIds: z.array(z.string()),
    bbox: BBOX_SCHEMA.optional(),
  }).strict()).optional(),
  tiers: z.array(z.object({
    name: z.string(),
    rank: z.number().int().positive(),
    artists: z.array(z.string()),
    sourceBlockIds: z.array(z.string()),
    bbox: BBOX_SCHEMA.optional(),
  }).strict()).optional(),
  schedule: z.array(z.object({
    artist: z.string().optional(),
    date: z.string().optional(),
    time: z.string().optional(),
    stage: z.string().optional(),
    venue: z.string().optional(),
    sourceBlockIds: z.array(z.string()),
    bbox: BBOX_SCHEMA.optional(),
  }).strict()).optional(),
  engine: z.string().optional(),
  source: z.enum(["local", "nim", "hook"]).optional(),
}).strict();

const DEFAULT_CACHE_DIR = join(tmpdir(), "festival-bloomberg", "parser-cache");
const DEFAULT_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1";
const DEFAULT_NIM_ENDPOINT = "/chat/completions";
const DEFAULT_NIM_MODEL = "meta/llama-3.1-70b-instruct";
const PROMPT_VERSION = "layout-v1";

/** Extract JSON-LD script blocks (application/ld+json). */
export function extractJsonLd(html: string): JsonLdNode[] {
  const out: JsonLdNode[] = [];
  const re =
    /<script[^>]*type\s*=\s*["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const raw = decodeBasicEntities(m[1].trim());
    if (!raw) continue;
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          if (item && typeof item === "object") out.push(item as JsonLdNode);
        }
      } else if (parsed && typeof parsed === "object") {
        out.push(parsed as JsonLdNode);
      }
    } catch {
      // ignore malformed JSON-LD
    }
  }
  return out;
}

export function extractMeta(html: string): MetaTags {
  const og: Record<string, string> = {};
  const twitter: Record<string, string> = {};
  const other: Record<string, string> = {};

  const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = titleMatch ? collapseWs(decodeBasicEntities(titleMatch[1])) : undefined;

  const metaRe = /<meta\b([^>]+)>/gi;
  let m: RegExpExecArray | null;
  while ((m = metaRe.exec(html)) !== null) {
    const attrs = parseAttrs(m[1]);
    const name = (attrs.name || attrs.property || attrs.itemprop || "").toLowerCase();
    const content = attrs.content ?? attrs.value;
    if (!name || content == null) continue;
    if (name.startsWith("og:")) og[name.slice(3)] = content;
    else if (name.startsWith("twitter:")) twitter[name.slice(8)] = content;
    else other[name] = content;
  }

  return {
    title,
    description: other.description ?? og.description,
    og,
    twitter,
    other,
  };
}

export function extractHeadings(html: string): string[] {
  const out: string[] = [];
  const re = /<h[1-3][^>]*>([\s\S]*?)<\/h[1-3]>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const text = collapseWs(stripTags(decodeBasicEntities(m[1])));
    if (text) out.push(text);
  }
  return out;
}

/** Pull artist-like entities from JSON-LD MusicEvent / performer nodes and semantic tags. */
export function extractArtistHints(html: string, jsonLd: JsonLdNode[] = extractJsonLd(html)): SemanticArtistHint[] {
  const artists: SemanticArtistHint[] = [];
  const seen = new Set<string>();

  const add = (name: string, source: SemanticArtistHint["source"], url?: string) => {
    const key = name.trim().toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    artists.push({ name: name.trim(), source, url });
  };

  const walk = (node: unknown): void => {
    if (!node) return;
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (typeof node !== "object") return;
    const o = node as JsonLdNode;
    const type = String(o["@type"] ?? "");
    if (/MusicGroup|Person|PerformingGroup|MusicArtist/i.test(type) && typeof o.name === "string") {
      add(o.name, "jsonld", typeof o.url === "string" ? o.url : undefined);
    }
    if (o.performer) walk(o.performer);
    if (o.performers) walk(o.performers);
    if (o.byArtist) walk(o.byArtist);
    if (Array.isArray(o.itemListElement)) walk(o.itemListElement);
    if (o.item) walk(o.item);
  };
  jsonLd.forEach(walk);

  const itempropRe =
    /<(?:span|a|div|li)[^>]*itemprop\s*=\s*["'](?:performer|byArtist|name)["'][^>]*>([\s\S]*?)<\//gi;
  let m: RegExpExecArray | null;
  while ((m = itempropRe.exec(html)) !== null) {
    const text = collapseWs(stripTags(decodeBasicEntities(m[1])));
    if (text) add(text, "semantic");
  }

  return artists;
}

export function parseHtml(html: string): ParsedPage {
  const jsonLd = extractJsonLd(html);
  return {
    jsonLd,
    meta: extractMeta(html),
    artists: extractArtistHints(html, jsonLd),
    headings: extractHeadings(html),
  };
}

/** Parse JSON, JSONL, TSV, or plain OCR text into normalized text blocks. */
export function parseOcrTextSegments(raw: string): OcrParseResult {
  const textBlocks: OcrParseResult["textBlocks"] = [];
  const seen = new Set<string>();
  const add = (text: unknown, bbox?: BBox, confidence?: number) => {
    if (typeof text !== "string") return;
    const clean = collapseWs(text).slice(0, 2_000);
    if (!clean) return;
    const key = `${clean}\0${bbox ? `${bbox.x},${bbox.y},${bbox.w},${bbox.h}` : ""}`;
    if (seen.has(key) || textBlocks.length >= 500) return;
    seen.add(key);
    textBlocks.push({ text: clean, ...(confidence === undefined ? {} : { confidence }), ...(bbox ? { bbox } : {}) });
  };

  try {
    const parsed = JSON.parse(raw) as unknown;
    collectOcrNodes(parsed, add);
  } catch {
    // Non-JSON OCR formats are handled below.
  }

  if (textBlocks.length === 0) {
    for (const line of raw.split(/\r?\n/)) {
      if (!line.trim()) continue;
      const tsv = line.split("\t");
      if (
        tsv.length >= 12
        && tsv.slice(6, 11).every((value) => Number.isFinite(Number(value)))
      ) {
        const confidence = normalizeConfidence(Number(tsv[10]));
        add(tsv.slice(11).join(" "), normalizeBBox({
          x: Number(tsv[6]), y: Number(tsv[7]), w: Number(tsv[8]), h: Number(tsv[9]),
        }), confidence);
        continue;
      }
      const positioned = line.match(
        /^\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]\s*(.+)$/,
      );
      if (positioned) {
        add(positioned[5], normalizeBBox({
          x: Number(positioned[1]), y: Number(positioned[2]),
          w: Number(positioned[3]), h: Number(positioned[4]),
        }));
      } else {
        add(line);
      }
    }
  }

  return { textBlocks, engine: "local-text" };
}

/** Run a supplied OCR engine or locally parse OCR text exported by another tool. */
export async function runOcrPlaceholder(
  image: Uint8Array | string,
  hook?: OcrHook,
  options: LayoutAnalysisOptions = {},
): Promise<OcrParseResult> {
  const bytes = await materializeInput(image);
  const cacheKey = digest(bytes, hook ? hook.toString() : "local-text", "ocr-v1");
  const cached = await readCache(options.cacheDir, `ocr-${cacheKey}`, OCR_SCHEMA);
  if (cached) return cached;

  const result = hook
    ? normalizeOcrResult(await hook(image), "hook")
    : parseOcrTextSegments(decodeText(bytes));
  await writeCache(options.cacheDir, `ocr-${cacheKey}`, result);
  return result;
}

/** Analyze layout locally, optionally enriching it with a validated NIM response. */
export async function runPosterLayoutPlaceholder(
  image: Uint8Array | string,
  hook?: PosterLayoutHook,
  options: LayoutAnalysisOptions = {},
): Promise<PosterLayoutResult> {
  const bytes = await materializeInput(image);
  if (hook) {
    const key = digest(bytes, hook.toString(), "poster-hook-v1");
    const cached = await readCache(options.cacheDir, `layout-${key}`, POSTER_SCHEMA);
    if (cached) return cached;
    const hooked = POSTER_SCHEMA.parse({ ...await hook(image), source: "hook" });
    await writeCache(options.cacheDir, `layout-${key}`, hooked);
    return hooked;
  }

  const ocr = options.ocr ?? await runOcrPlaceholder(image, undefined, options);
  const local = analyzeLocalLayout(ocr);
  const config = resolveNimConfig(options.nim);
  const noPaidRequests = options.no_paid_requests === true
    || envFlag(process.env.NO_PAID_REQUESTS)
    || envFlag(process.env.no_paid_requests);
  if (noPaidRequests || !config.apiKey || ocr.textBlocks.length === 0) return local;

  const key = digest(
    JSON.stringify(ocr.textBlocks),
    config.baseUrl,
    config.endpoint,
    config.model,
    PROMPT_VERSION,
  );
  const cached = await readCache(options.cacheDir, `layout-${key}`, POSTER_SCHEMA);
  if (cached) return cached;

  try {
    const extracted = await extractLayoutWithNim(ocr, options.nim);
    if (!extracted) return local;
    const result: PosterLayoutResult = {
      ...local,
      ...extracted,
      regions: local.regions,
      engine: `nvidia-nim:${config.model}`,
      source: "nim",
    };
    await writeCache(options.cacheDir, `layout-${key}`, result);
    return result;
  } catch {
    return local;
  }
}

export async function parsePageWithHooks(
  html: string,
  opts?: { image?: Uint8Array | string; hooks?: ParserHooks; layout?: LayoutAnalysisOptions },
): Promise<ParsedPage> {
  const base = parseHtml(html);
  if (!opts?.image) return base;
  const ocr = await runOcrPlaceholder(opts.image, opts.hooks?.ocr, opts.layout);
  const posterLayout = await runPosterLayoutPlaceholder(
    opts.image,
    opts.hooks?.posterLayout,
    { ...opts.layout, ocr },
  );
  return { ...base, ocr, posterLayout };
}

/** OpenAI-compatible NVIDIA NIM client with conservative input and output caps. */
export async function extractLayoutWithNim(
  ocr: OcrParseResult,
  options: NimClientOptions = {},
): Promise<Pick<PosterLayoutResult, "artists" | "tiers" | "schedule"> | undefined> {
  const config = resolveNimConfig(options);
  if (!config.apiKey) return undefined;
  const { messages, maxOutputTokens } = buildNimMessages(ocr, config.maxInputTokens, config.maxOutputTokens);
  const request = config.fetch ?? globalThis.fetch;
  const response = await request(resolveEndpoint(config.baseUrl, config.endpoint), {
    method: "POST",
    headers: {
      authorization: `Bearer ${config.apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: config.model,
      messages,
      temperature: 0,
      max_tokens: maxOutputTokens,
      stream: false,
    }),
    signal: AbortSignal.timeout(config.timeoutMs),
  });
  if (!response.ok) throw new Error(`NVIDIA NIM request failed (${response.status})`);
  const payload = await response.json() as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  const content = payload.choices?.[0]?.message?.content;
  if (typeof content !== "string") throw new Error("NVIDIA NIM returned no message content");
  const parsed = NIM_EXTRACTION_SCHEMA.parse(parseJsonObject(content));
  return validateNimEvidence(parsed, ocr.textBlocks);
}

function collectOcrNodes(
  root: unknown,
  add: (text: unknown, bbox?: BBox, confidence?: number) => void,
): void {
  const visited = new Set<object>();
  let count = 0;
  const visit = (node: unknown, depth: number): void => {
    if (depth > 10 || count >= 2_000 || node == null) return;
    if (typeof node === "string") {
      add(node);
      return;
    }
    if (typeof node !== "object") return;
    if (visited.has(node)) return;
    visited.add(node);
    count += 1;
    if (Array.isArray(node)) {
      node.forEach((item) => visit(item, depth + 1));
      return;
    }
    const object = node as Record<string, unknown>;
    const text = object.text ?? object.value ?? object.description;
    if (typeof text === "string") {
      const bbox = normalizeBBox(
        object.bbox ?? object.boundingBox ?? object.box ?? object.rect
        ?? object.boundingPoly ?? object.geometry,
      );
      const confidence = normalizeConfidence(
        Number(object.confidence ?? object.score ?? object.conf ?? Number.NaN),
      );
      add(text, bbox, confidence);
    }
    for (const value of Object.values(object)) {
      if (value && typeof value === "object") visit(value, depth + 1);
    }
  };
  visit(root, 0);
}

function normalizeBBox(value: unknown): BBox | undefined {
  if (typeof value === "string") {
    const numbers = value.match(/-?\d+(?:\.\d+)?/g)?.map(Number);
    if (numbers) return normalizeBBox(numbers);
  }
  if (Array.isArray(value)) {
    const numbers = value.map(Number);
    if (numbers.length === 4) {
      return normalizeBBox({ x: numbers[0], y: numbers[1], w: numbers[2], h: numbers[3] });
    }
    if (numbers.length >= 8 && numbers.every(Number.isFinite)) {
      const xs = numbers.filter((_, index) => index % 2 === 0);
      const ys = numbers.filter((_, index) => index % 2 === 1);
      return normalizeBBox({
        x: Math.min(...xs), y: Math.min(...ys),
        w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys),
      });
    }
  }
  if (!value || typeof value !== "object") return undefined;
  const object = value as Record<string, unknown>;
  const vertices = object.vertices ?? object.points;
  if (Array.isArray(vertices)) {
    const points = vertices
      .map((point) => point as Record<string, unknown>)
      .filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
    if (points.length >= 2) {
      const xs = points.map((point) => Number(point.x));
      const ys = points.map((point) => Number(point.y));
      return normalizeBBox({
        x: Math.min(...xs), y: Math.min(...ys),
        w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys),
      });
    }
  }
  const x = Number(object.x ?? object.left);
  const y = Number(object.y ?? object.top);
  const w = Number(object.w ?? object.width);
  const h = Number(object.h ?? object.height);
  const parsed = BBOX_SCHEMA.safeParse({ x, y, w, h });
  return parsed.success ? parsed.data : undefined;
}

function normalizeConfidence(value: number): number | undefined {
  if (!Number.isFinite(value) || value < 0) return undefined;
  const normalized = value > 1 ? value / 100 : value;
  return normalized <= 1 ? normalized : undefined;
}

function normalizeOcrResult(result: OcrParseResult, fallbackEngine: string): OcrParseResult {
  const normalized = result.textBlocks.slice(0, 500).flatMap((block) => {
    const text = collapseWs(block.text).slice(0, 2_000);
    if (!text) return [];
    const bbox = normalizeBBox(block.bbox);
    const confidence = normalizeConfidence(Number(block.confidence));
    return [{ text, ...(confidence === undefined ? {} : { confidence }), ...(bbox ? { bbox } : {}) }];
  });
  return OCR_SCHEMA.parse({ textBlocks: normalized, engine: result.engine ?? fallbackEngine });
}

function analyzeLocalLayout(ocr: OcrParseResult): PosterLayoutResult {
  const blocks = ocr.textBlocks.map((block, index) => ({ ...block, id: `b${index}` }));
  const candidates = blocks.filter((block) => isArtistCandidate(block.text));
  const tierLabels = blocks.filter((block) => tierLabel(block.text));
  const tierFor = new Map<string, string>();
  const tiers = new Map<string, LayoutTier>();

  for (const block of candidates) {
    const precedingLabel = tierLabels
      .filter((label) => (label.bbox?.y ?? -1) <= (block.bbox?.y ?? Number.MAX_SAFE_INTEGER))
      .sort((a, b) => (b.bbox?.y ?? -1) - (a.bbox?.y ?? -1))[0];
    const rank = precedingLabel
      ? tierLabels.indexOf(precedingLabel) + 1
      : inferSizeRank(block, candidates);
    const name = precedingLabel?.text ?? `tier-${rank}`;
    tierFor.set(block.id, name);
    const existing = tiers.get(name) ?? {
      name,
      rank,
      artists: [],
      sourceBlockIds: precedingLabel ? [precedingLabel.id] : [],
    };
    existing.artists.push(block.text);
    existing.sourceBlockIds.push(block.id);
    tiers.set(name, existing);
  }

  const artists: LayoutArtist[] = candidates.map((block) => ({
    name: block.text,
    tier: tierFor.get(block.id),
    sourceBlockIds: [block.id],
    ...(block.bbox ? { bbox: block.bbox } : {}),
  }));
  const tierResults = [...tiers.values()]
    .sort((a, b) => a.rank - b.rank)
    .map((tier) => ({ ...tier, bbox: unionBBoxes(tier.sourceBlockIds, blocks) }))
    .map(({ bbox, ...tier }) => ({ ...tier, ...(bbox ? { bbox } : {}) }));

  return {
    regions: blocks.flatMap((block) => block.bbox ? [{
      role: inferRegionRole(block.text),
      bbox: block.bbox,
      text: block.text,
    }] : []),
    artists,
    tiers: tierResults,
    schedule: blocks.flatMap((block) => localSchedule(block)),
    engine: "local-layout",
    source: "local",
  };
}

function inferRegionRole(text: string): PosterLayoutResult["regions"][number]["role"] {
  if (extractDate(text) || extractTime(text)) return "date";
  if (/\b(?:venue|stage|tent|arena|hall|park)\b/i.test(text)) return "venue";
  if (tierLabel(text) || /\b(?:festival|presents|lineup)\b/i.test(text)) return "headline";
  return isArtistCandidate(text) ? "artist" : "other";
}

function isArtistCandidate(text: string): boolean {
  return text.length <= 100
    && /[\p{L}]/u.test(text)
    && text.split(/\s+/).length <= 10
    && !extractDate(text)
    && !extractTime(text)
    && !/\b(?:festival|presents|lineup|headliners?|support|venue|stage|tent|arena|hall|park|tickets?|doors?|admission|www\.|https?:)\b/i.test(text);
}

function tierLabel(text: string): string | undefined {
  return /^(?:headliners?|top billing|support|second line|undercard|lineup)$/i.test(text.trim())
    ? text.trim()
    : undefined;
}

function inferSizeRank(
  block: { bbox?: BBox },
  candidates: Array<{ bbox?: BBox }>,
): number {
  if (!block.bbox) return 1;
  const heights = [...new Set(
    candidates.flatMap((candidate) => candidate.bbox ? [Math.round(candidate.bbox.h)] : []),
  )].sort((a, b) => b - a);
  const rank = heights.findIndex((height) => Math.abs(height - block.bbox!.h) <= Math.max(2, height * 0.15));
  return rank < 0 ? 1 : rank + 1;
}

function localSchedule(
  block: { id: string; text: string; bbox?: BBox },
): ScheduleDetail[] {
  const date = extractDate(block.text);
  const time = extractTime(block.text);
  if (!date && !time) return [];
  let remainder = block.text;
  if (date) remainder = remainder.replace(date, " ");
  if (time) remainder = remainder.replace(time, " ");
  const stage = remainder.match(/\b(?:[\p{L}\d&'’-]+\s+){0,2}(?:stage|tent)\b/iu)?.[0];
  if (stage) remainder = remainder.replace(stage, " ");
  const artist = collapseWs(remainder.replace(/^[\s|,:;–—-]+|[\s|,:;–—-]+$/g, ""));
  return [{
    ...(artist && isArtistCandidate(artist) ? { artist } : {}),
    ...(date ? { date } : {}),
    ...(time ? { time } : {}),
    ...(stage ? { stage } : {}),
    sourceBlockIds: [block.id],
    ...(block.bbox ? { bbox: block.bbox } : {}),
  }];
}

function extractDate(text: string): string | undefined {
  return text.match(
    /\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b/i,
  )?.[0];
}

function extractTime(text: string): string | undefined {
  return text.match(/\b(?:\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?))\b/i)?.[0];
}

function buildNimMessages(
  ocr: OcrParseResult,
  inputCap: number,
  outputCap: number,
): {
  messages: Array<{ role: "system" | "user"; content: string }>;
  maxOutputTokens: number;
} {
  const maxInputTokens = clampInt(inputCap, 128, 8_192);
  const maxOutputTokens = clampInt(outputCap, 1, 2_048);
  const system = "Extract festival-poster facts from OCR blocks. Return JSON only. Copy fact strings exactly; never correct, merge, or invent them. Derive grouping/rank only from coordinates and text size. Every item must cite supporting block ids. Coordinates belong to ids and must not be rewritten. Missing evidence means empty arrays.";
  const prefix = "Schema: {artists:[{name,tier?,sourceBlockIds[]}],tiers:[{name,rank,artists[],sourceBlockIds[]}],schedule:[{artist?,date?,time?,stage?,venue?,sourceBlockIds[]}]}. Blocks (id|x,y,w,h|text):\n";
  const budget = maxInputTokens - 32 - Buffer.byteLength(system) - Buffer.byteLength(prefix);
  if (budget < 0) throw new Error("NVIDIA NIM input token cap is too small for the extraction schema");
  const rows: string[] = [];
  let used = 0;
  for (const [index, block] of ocr.textBlocks.entries()) {
    const coordinates = block.bbox
      ? `${block.bbox.x},${block.bbox.y},${block.bbox.w},${block.bbox.h}`
      : "-";
    const base = `b${index}|${coordinates}|`;
    const available = budget - used - Buffer.byteLength(base) - 1;
    if (available <= 0) break;
    const text = truncateUtf8(block.text.replace(/[\r\n\t|]+/g, " "), available);
    if (!text) break;
    const row = `${base}${text}\n`;
    rows.push(row);
    used += Buffer.byteLength(row);
  }
  return {
    messages: [
      { role: "system", content: system },
      { role: "user", content: prefix + rows.join("") },
    ],
    maxOutputTokens,
  };
}

function validateNimEvidence(
  extraction: z.infer<typeof NIM_EXTRACTION_SCHEMA>,
  blocks: OcrParseResult["textBlocks"],
): Pick<PosterLayoutResult, "artists" | "tiers" | "schedule"> {
  const blockMap = new Map(blocks.map((block, index) => [`b${index}`, block]));
  const validIds = (ids: string[]) => ids.every((id) => blockMap.has(id));
  const evidence = (ids: string[]) => ids.map((id) => blockMap.get(id)?.text ?? "").join(" ").toLowerCase();
  const supported = (value: string | undefined, ids: string[]) => !value
    || evidence(ids).includes(value.toLowerCase());
  const bbox = (ids: string[]) => unionBBoxes(
    ids,
    blocks.map((block, index) => ({ ...block, id: `b${index}` })),
  );

  const artists = extraction.artists
    .filter((artist) => validIds(artist.sourceBlockIds)
      && supported(artist.name, artist.sourceBlockIds)
      && supported(artist.tier, artist.sourceBlockIds))
    .map((artist) => ({ ...artist, bbox: bbox(artist.sourceBlockIds) }))
    .map(({ bbox: box, ...artist }) => ({ ...artist, ...(box ? { bbox: box } : {}) }));
  const tiers = extraction.tiers
    .filter((tier) => validIds(tier.sourceBlockIds)
      && supported(tier.name, tier.sourceBlockIds)
      && tier.artists.every((artist) => supported(artist, tier.sourceBlockIds)))
    .map((tier) => ({ ...tier, bbox: bbox(tier.sourceBlockIds) }))
    .map(({ bbox: box, ...tier }) => ({ ...tier, ...(box ? { bbox: box } : {}) }));
  const schedule = extraction.schedule
    .filter((item) => validIds(item.sourceBlockIds)
      && [item.artist, item.date, item.time, item.stage, item.venue]
        .every((value) => supported(value, item.sourceBlockIds)))
    .map((item) => ({ ...item, bbox: bbox(item.sourceBlockIds) }))
    .map(({ bbox: box, ...item }) => ({ ...item, ...(box ? { bbox: box } : {}) }));
  return { artists, tiers, schedule };
}

function unionBBoxes(
  ids: string[],
  blocks: Array<{ id: string; bbox?: BBox }>,
): BBox | undefined {
  const boxes = ids.flatMap((id) => {
    const box = blocks.find((block) => block.id === id)?.bbox;
    return box ? [box] : [];
  });
  if (boxes.length === 0) return undefined;
  const x = Math.min(...boxes.map((box) => box.x));
  const y = Math.min(...boxes.map((box) => box.y));
  const right = Math.max(...boxes.map((box) => box.x + box.w));
  const bottom = Math.max(...boxes.map((box) => box.y + box.h));
  return { x, y, w: right - x, h: bottom - y };
}

function resolveNimConfig(options: NimClientOptions = {}) {
  return {
    baseUrl: options.baseUrl ?? process.env.NVIDIA_NIM_BASE_URL ?? DEFAULT_NIM_BASE_URL,
    endpoint: options.endpoint ?? process.env.NVIDIA_NIM_ENDPOINT ?? DEFAULT_NIM_ENDPOINT,
    model: options.model ?? process.env.NVIDIA_NIM_MODEL ?? DEFAULT_NIM_MODEL,
    apiKey: options.apiKey ?? process.env.NVIDIA_API_KEY,
    maxInputTokens: options.maxInputTokens
      ?? envNumber(process.env.NVIDIA_NIM_MAX_INPUT_TOKENS, 4_096),
    maxOutputTokens: options.maxOutputTokens
      ?? envNumber(process.env.NVIDIA_NIM_MAX_OUTPUT_TOKENS, 800),
    timeoutMs: clampInt(
      options.timeoutMs ?? envNumber(process.env.NVIDIA_NIM_TIMEOUT_MS, 20_000),
      1_000,
      120_000,
    ),
    fetch: options.fetch,
  };
}

function resolveEndpoint(baseUrl: string, endpoint: string): string {
  if (/^https?:\/\//i.test(endpoint)) return endpoint;
  return `${baseUrl.replace(/\/+$/, "")}/${endpoint.replace(/^\/+/, "")}`;
}

function parseJsonObject(content: string): unknown {
  const unfenced = content.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
  const start = unfenced.indexOf("{");
  const end = unfenced.lastIndexOf("}");
  if (start < 0 || end < start) throw new Error("NVIDIA NIM returned invalid JSON");
  return JSON.parse(unfenced.slice(start, end + 1)) as unknown;
}

async function materializeInput(input: Uint8Array | string): Promise<Buffer> {
  if (typeof input !== "string") return Buffer.from(input);
  if (/[\r\n]|^\s*[\[{]/.test(input)) return Buffer.from(input);
  try {
    return await readFile(input);
  } catch {
    return Buffer.from(input);
  }
}

function decodeText(bytes: Uint8Array): string {
  if (bytes.length === 0 || bytes.includes(0)) return "";
  const decoded = Buffer.from(bytes).toString("utf8");
  const replacementCount = decoded.match(/\uFFFD/g)?.length ?? 0;
  return replacementCount > Math.max(1, decoded.length * 0.01) ? "" : decoded;
}

async function readCache<T>(
  cacheDir: string | undefined,
  key: string,
  schema: z.ZodType<T>,
): Promise<T | undefined> {
  try {
    const parsed = JSON.parse(
      await readFile(join(cacheDir || DEFAULT_CACHE_DIR, `${key}.json`), "utf8"),
    ) as unknown;
    const validated = schema.safeParse(parsed);
    return validated.success ? validated.data : undefined;
  } catch {
    return undefined;
  }
}

async function writeCache(cacheDir: string | undefined, key: string, value: unknown): Promise<void> {
  const directory = cacheDir || DEFAULT_CACHE_DIR;
  const destination = join(directory, `${key}.json`);
  const temporary = `${destination}.${process.pid}.${Date.now()}.tmp`;
  try {
    await mkdir(directory, { recursive: true });
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600 });
    await rename(temporary, destination);
  } catch {
    // Cache failures never make parsing fail.
  }
}

function digest(...values: Array<string | Uint8Array>): string {
  const hash = createHash("sha256");
  values.forEach((value) => hash.update(value));
  return hash.digest("hex");
}

function truncateUtf8(value: string, maxBytes: number): string {
  if (Buffer.byteLength(value) <= maxBytes) return value;
  let low = 0;
  let high = value.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (Buffer.byteLength(value.slice(0, middle)) <= maxBytes) low = middle;
    else high = middle - 1;
  }
  return value.slice(0, low);
}

function clampInt(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.floor(Number.isFinite(value) ? value : min)));
}

function envNumber(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function envFlag(value: string | undefined): boolean {
  return /^(?:1|true|yes|on)$/i.test(value ?? "");
}

function parseAttrs(raw: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  const re = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(raw)) !== null) {
    const key = m[1].toLowerCase();
    if (key === "/" || key === "") continue;
    attrs[key] = m[2] ?? m[3] ?? m[4] ?? "";
  }
  return attrs;
}

function stripTags(s: string): string {
  return s.replace(/<[^>]+>/g, " ");
}

function collapseWs(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function decodeBasicEntities(s: string): string {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ");
}
