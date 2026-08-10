import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, it } from "node:test";
import {
  WikimediaPageviewsClient,
  buildPageviewsUrl,
  encodePageviewsArticleTitle,
  parsePageviewsResponse,
} from "../../src/scraper/wikimedia_pageviews";

const fixture = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../tests/fixtures/intelligence/pageviews_radiohead.json"),
    "utf8",
  ),
) as {
  project: string;
  article: string;
  access: string;
  agent: string;
  granularity: string;
  start: string;
  end: string;
  response: unknown;
};

describe("wikimedia pageviews adapter contract", () => {
  it("encodes article titles with spaces and reserved characters", () => {
    assert.equal(encodePageviewsArticleTitle("Radiohead"), "Radiohead");
    assert.equal(
      encodePageviewsArticleTitle("Are You the One?"),
      "Are_You_the_One%3F",
    );
    assert.equal(
      encodePageviewsArticleTitle("  Beyoncé  "),
      "Beyonc%C3%A9",
    );
  });

  it("builds the documented per-article path", () => {
    const { url, encodedArticle } = buildPageviewsUrl({
      project: "en.wikipedia",
      access: "all-access",
      agent: "user",
      articleTitle: "Are You the One?",
      granularity: "daily",
      start: "20260101",
      end: "20260131",
    });
    assert.equal(encodedArticle, "Are_You_the_One%3F");
    assert.equal(
      url,
      "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/" +
        "en.wikipedia/all-access/user/Are_You_the_One%3F/daily/20260101/20260131",
    );
  });

  it("parses the fixture response shape and sums views", () => {
    const parsed = parsePageviewsResponse(fixture.response);
    assert.equal(parsed.items.length, 3);
    assert.equal(parsed.valueSum, 3000);
    assert.equal(parsed.items[0]?.views, 1000);
  });

  it("fetches via injected fetch without live network", async () => {
    const calls: string[] = [];
    const client = new WikimediaPageviewsClient({
      timeoutMs: 1000,
      now: () => new Date("2026-02-01T00:00:00.000Z"),
      rateLimiter: undefined,
      fetchImpl: (async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        return new Response(JSON.stringify(fixture.response), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }) as typeof fetch,
      domainLimits: {
        tokensPerSecond: 100,
        bucketSize: 10,
        minSpacingMs: 0,
        maxRetries: 0,
        baseBackoffMs: 1,
        maxBackoffMs: 1,
      },
    });

    const result = await client.fetchPerArticle({
      articleTitle: fixture.article,
      project: fixture.project,
      access: "all-access",
      agent: "user",
      granularity: "daily",
      start: fixture.start,
      end: fixture.end,
      artistKey: "mbid::radiohead",
    });

    assert.equal(result.ok, true);
    assert.equal(result.status, "ok");
    assert.equal(result.valueSum, 3000);
    assert.equal(result.httpStatus, 200);
    assert.match(calls[0] ?? "", /\/per-article\/en\.wikipedia\/all-access\/user\/Radiohead\/daily\/20260101\/20260103$/);

    const observation = client.toAttentionObservation(result, {
      artistKey: "mbid::radiohead",
      festivalKey: "coachella",
      editionKey: "coachella_2026",
      editionYear: 2026,
    });
    assert.equal(observation.status, "ok");
    assert.equal(observation.value_sum, 3000);
    assert.equal(observation.source_system, "wikimedia");
    assert.ok(observation.source_url.includes("wikimedia.org"));
    assert.equal(observation.period_start, "2026-01-01");
    assert.equal(observation.period_end, "2026-01-03");
  });

  it("persists error status for non-retryable HTTP failures", async () => {
    const client = new WikimediaPageviewsClient({
      now: () => new Date("2026-02-01T00:00:00.000Z"),
      fetchImpl: (async () =>
        new Response(JSON.stringify({ type: "https://mediawiki.org/wiki/HyperSwitch/errors/not_found" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        })) as typeof fetch,
      domainLimits: {
        tokensPerSecond: 100,
        bucketSize: 10,
        minSpacingMs: 0,
        maxRetries: 0,
        baseBackoffMs: 1,
        maxBackoffMs: 1,
      },
    });

    const result = await client.fetchPerArticle({
      articleTitle: "Definitely Not A Page",
      start: "20260101",
      end: "20260102",
    });
    assert.equal(result.ok, false);
    assert.equal(result.status, "missing");
    assert.equal(result.errorCode, "pageviews_not_found");
    assert.equal(result.valueSum, null);
  });
});
