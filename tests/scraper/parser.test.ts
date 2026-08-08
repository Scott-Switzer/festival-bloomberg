import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import {
  extractJsonLd,
  extractMeta,
  parseHtml,
  parseOcrTextSegments,
  runOcrPlaceholder,
  runPosterLayoutPlaceholder,
} from "../../src/scraper/parser";

const SAMPLE = `
<html><head>
<title>Fest Lineup</title>
<meta name="description" content="Weekend one">
<meta property="og:title" content="Fest">
<script type="application/ld+json">
{"@type":"MusicEvent","performer":[{"@type":"MusicGroup","name":"Artist One"}]}
</script>
</head><body>
<h1>Lineup</h1>
<span itemprop="performer">Artist Two</span>
</body></html>
`;

describe("parser", () => {
  it("extracts JSON-LD performers and meta", () => {
    const jsonLd = extractJsonLd(SAMPLE);
    assert.equal(jsonLd.length, 1);
    const meta = extractMeta(SAMPLE);
    assert.equal(meta.title, "Fest Lineup");
    assert.equal(meta.og.title, "Fest");
    const page = parseHtml(SAMPLE);
    assert.ok(page.artists.some((a) => a.name === "Artist One"));
    assert.ok(page.artists.some((a) => a.name === "Artist Two"));
  });

  it("returns a safe empty local OCR result for binary input", async () => {
    const ocr = await runOcrPlaceholder(new Uint8Array());
    assert.equal(ocr.engine, "local-text");
    assert.equal(ocr.textBlocks.length, 0);
  });

  it("parses OCR segments and bounding boxes from JSON and positioned text", () => {
    const json = parseOcrTextSegments(JSON.stringify({
      lines: [{ text: "Artist One", confidence: 98, boundingBox: [10, 20, 120, 30] }],
    }));
    assert.deepEqual(json.textBlocks[0], {
      text: "Artist One",
      confidence: 0.98,
      bbox: { x: 10, y: 20, w: 120, h: 30 },
    });

    const positioned = parseOcrTextSegments("[5, 6, 70, 12] Artist Two");
    assert.deepEqual(positioned.textBlocks[0]?.bbox, { x: 5, y: 6, w: 70, h: 12 });
  });

  it("derives local artist tiers and schedule without model calls", async () => {
    let calls = 0;
    const ocr = parseOcrTextSegments(JSON.stringify([
      { text: "HEADLINERS", bbox: [0, 0, 200, 30] },
      { text: "Artist One", bbox: [0, 40, 180, 24] },
      { text: "Artist Two", bbox: [0, 70, 160, 20] },
      { text: "Artist One — Friday 8:00 PM", bbox: [220, 40, 220, 20] },
    ]));
    const layout = await runPosterLayoutPlaceholder("unused", undefined, {
      ocr,
      no_paid_requests: true,
      nim: {
        apiKey: "test-key",
        fetch: async () => {
          calls += 1;
          throw new Error("must not be called");
        },
      },
    });

    assert.equal(calls, 0);
    assert.equal(layout.source, "local");
    assert.deepEqual(layout.artists?.map((artist) => artist.name), ["Artist One", "Artist Two"]);
    assert.equal(layout.tiers?.[0]?.name, "HEADLINERS");
    assert.deepEqual(layout.schedule?.[0], {
      artist: "Artist One",
      date: "Friday",
      time: "8:00 PM",
      sourceBlockIds: ["b3"],
      bbox: { x: 220, y: 40, w: 220, h: 20 },
    });
  });

  it("uses cached OCR hook results", async () => {
    const cacheDir = await mkdtemp(join(tmpdir(), "parser-ocr-test-"));
    let calls = 0;
    const hook = async () => {
      calls += 1;
      return { textBlocks: [{ text: "Cached Artist" }], engine: "mock-ocr" };
    };
    try {
      const first = await runOcrPlaceholder("poster-bytes", hook, { cacheDir });
      const second = await runOcrPlaceholder("poster-bytes", hook, { cacheDir });
      assert.equal(calls, 1);
      assert.deepEqual(second, first);
    } finally {
      await rm(cacheDir, { recursive: true, force: true });
    }
  });

  it("validates and caches OpenAI-compatible NIM layout responses within token caps", async () => {
    const cacheDir = await mkdtemp(join(tmpdir(), "parser-nim-test-"));
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
    const mockFetch: typeof fetch = async (input, init) => {
      requests.push({
        url: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return new Response(JSON.stringify({
        choices: [{
          message: {
            content: JSON.stringify({
              artists: [{
                name: "Artist One",
                tier: "HEADLINERS",
                sourceBlockIds: ["b0", "b1"],
              }],
              tiers: [{
                name: "HEADLINERS",
                rank: 1,
                artists: ["Artist One"],
                sourceBlockIds: ["b0", "b1"],
              }],
              schedule: [{
                date: "Friday",
                time: "8:00 PM",
                sourceBlockIds: ["b2"],
              }],
            }),
          },
        }],
      }), { status: 200, headers: { "content-type": "application/json" } });
    };
    const ocr = parseOcrTextSegments(JSON.stringify([
      { text: "HEADLINERS", bbox: [0, 0, 200, 30] },
      { text: `Artist One ${"x".repeat(4_000)}`, bbox: [0, 40, 180, 24] },
      { text: "Friday 8:00 PM", bbox: [220, 40, 160, 20] },
    ]));
    const options = {
      cacheDir,
      ocr,
      nim: {
        apiKey: "test-key",
        baseUrl: "https://nim.example/v1",
        endpoint: "chat/completions",
        model: "test-model",
        maxInputTokens: 768,
        maxOutputTokens: 77,
        fetch: mockFetch,
      },
    };

    try {
      const first = await runPosterLayoutPlaceholder("unused", undefined, options);
      const second = await runPosterLayoutPlaceholder("unused", undefined, options);
      assert.equal(requests.length, 1);
      assert.equal(requests[0].url, "https://nim.example/v1/chat/completions");
      assert.equal(requests[0].body.max_tokens, 77);
      const messages = requests[0].body.messages as Array<{ content: string }>;
      const inputBytes = messages.reduce((sum, message) => sum + Buffer.byteLength(message.content), 32);
      assert.ok(inputBytes <= 768);
      assert.equal(first.source, "nim");
      assert.equal(first.artists?.[0]?.name, "Artist One");
      assert.deepEqual(first.artists?.[0]?.bbox, { x: 0, y: 0, w: 200, h: 64 });
      assert.deepEqual(second, first);
    } finally {
      await rm(cacheDir, { recursive: true, force: true });
    }
  });
});
