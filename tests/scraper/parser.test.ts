import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  extractJsonLd,
  extractMeta,
  parseHtml,
  runOcrPlaceholder,
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

  it("ocr placeholder returns empty engine marker", async () => {
    const ocr = await runOcrPlaceholder(new Uint8Array());
    assert.equal(ocr.engine, "placeholder");
    assert.equal(ocr.textBlocks.length, 0);
  });
});
