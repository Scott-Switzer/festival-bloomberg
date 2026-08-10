import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  canonicalJson,
  canonicalizeUrl,
  mergeEvidence,
  normalizeJson,
  normalizeText,
  stableHash,
} from "../../src/scraper/normalization";

describe("canonical normalization", () => {
  it("normalizes Unicode and explicit whitespace without case folding or HTML stripping", () => {
    assert.equal(
      normalizeText(" \u200BＦｅｓｔｉｖａｌ\u00a0\n  Lineup! "),
      "Festival Lineup!",
    );
    assert.equal(normalizeText("<B>Artist A</B>"), "<B>Artist A</B>");
  });

  it("canonicalizes JSON recursively and rejects ambiguous values", () => {
    const left = canonicalJson({
      title: " Lineup\u00a0Update ",
      artists: ["Ａ", "B"],
    });
    const right = canonicalJson({
      artists: ["A", "B"],
      title: "Lineup Update",
    });
    assert.equal(left, right);
    assert.deepEqual(normalizeJson({ negativeZero: -0 }), { negativeZero: 0 });
    assert.throws(() => normalizeJson({ value: Number.NaN }), /finite JSON/);
    assert.equal(
      canonicalJson(JSON.parse('{"__proto__":"kept"}')),
      '{"__proto__":"kept"}',
    );
    assert.equal(
      stableHash("fixture-v1", normalizeJson({ b: 2, a: 1 })),
      stableHash("fixture-v1", normalizeJson({ a: 1, b: 2 })),
    );
  });

  it("removes only fragments, credentials, and known tracking parameters", () => {
    assert.equal(
      canonicalizeUrl(
        "HTTPS://user:secret@EXAMPLE.COM:443/Lineup/?b=2&utm_source=x&a=1&FBCLID=y#top",
      ),
      "https://example.com/Lineup/?a=1&b=2",
    );
    assert.equal(
      canonicalizeUrl("https://example.com/lineup/?ref=partner"),
      "https://example.com/lineup/?ref=partner",
    );
    assert.throws(() => canonicalizeUrl("ftp://example.com/file"), /Unsupported/);
  });

  it("merges equivalent evidence deterministically using earliest fetch time", () => {
    const merged = mergeEvidence(
      [
        {
          url: "https://example.com/lineup?utm_source=x",
          selector: " #lineup ",
          snippet: " Artist A ",
          fetchedAt: "2026-01-02T00:00:00.000Z",
        },
      ],
      [
        {
          url: "https://EXAMPLE.com/lineup#artists",
          selector: "#lineup",
          snippet: "Artist A",
          fetchedAt: "2026-01-01T00:00:00.000Z",
        },
      ],
    );
    assert.deepEqual(merged, [
      {
        url: "https://example.com/lineup",
        selector: "#lineup",
        snippet: "Artist A",
        fetchedAt: "2026-01-01T00:00:00.000Z",
      },
    ]);
  });
});
