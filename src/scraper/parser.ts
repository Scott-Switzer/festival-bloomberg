/**
 * HTML parsing for JSON-LD, meta, and semantic tags.
 * Placeholder hooks for OCR / poster layout analysis.
 */

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

export type PosterLayoutResult = {
  regions: Array<{ role: "headline" | "artist" | "date" | "venue" | "other"; bbox: BBox; text?: string }>;
  engine?: string;
};

export type BBox = { x: number; y: number; w: number; h: number };

export type OcrHook = (image: Uint8Array | string) => Promise<OcrParseResult>;
export type PosterLayoutHook = (image: Uint8Array | string) => Promise<PosterLayoutResult>;

export type ParserHooks = {
  ocr?: OcrHook;
  posterLayout?: PosterLayoutHook;
};

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

/**
 * Placeholder OCR hook — returns empty result until an engine is wired.
 * Intentionally no heavy OCR dependency.
 */
export async function runOcrPlaceholder(
  _image: Uint8Array | string,
  hook?: OcrHook,
): Promise<OcrParseResult> {
  if (hook) return hook(_image);
  return { textBlocks: [], engine: "placeholder" };
}

/**
 * Placeholder poster-layout hook for future vision/layout analysis.
 */
export async function runPosterLayoutPlaceholder(
  _image: Uint8Array | string,
  hook?: PosterLayoutHook,
): Promise<PosterLayoutResult> {
  if (hook) return hook(_image);
  return { regions: [], engine: "placeholder" };
}

export async function parsePageWithHooks(
  html: string,
  opts?: { image?: Uint8Array | string; hooks?: ParserHooks },
): Promise<ParsedPage> {
  const base = parseHtml(html);
  if (!opts?.image) return base;
  const [ocr, posterLayout] = await Promise.all([
    runOcrPlaceholder(opts.image, opts.hooks?.ocr),
    runPosterLayoutPlaceholder(opts.image, opts.hooks?.posterLayout),
  ]);
  return { ...base, ocr, posterLayout };
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
