import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  parseCostEvent,
  parseObservation,
  ObservationSchema,
} from "../../src/scraper/schemas";

describe("scraper schemas", () => {
  it("parses a minimal observation", () => {
    const obs = parseObservation({
      id: "obs_1",
      kind: "lineup",
      sourceDomain: "www.coachella.com",
      url: "https://www.coachella.com/lineup",
      observedAt: "2026-04-01T12:00:00.000Z",
      payload: { slots: [] },
    });
    assert.equal(obs.kind, "lineup");
    assert.equal(obs.evidence.length, 0);
  });

  it("rejects invalid observation url", () => {
    assert.throws(() =>
      ObservationSchema.parse({
        id: "x",
        kind: "meta",
        sourceDomain: "a.com",
        url: "not-a-url",
        observedAt: "2026-04-01T12:00:00.000Z",
        payload: {},
      }),
    );
  });

  it("parses cost events with defaults", () => {
    const e = parseCostEvent({
      id: "c1",
      provider: "monid",
      operation: "monid-prod:/web/fetch-structured",
      at: "2026-04-01T12:00:00.000Z",
    });
    assert.equal(e.currency, "USD");
    assert.equal(e.totalCostUsd, 0);
  });
});
