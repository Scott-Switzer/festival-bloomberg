import { describe, expect, it } from "vitest";

import { isDeniedProductPath, isPublicDemoPathBlocked } from "./routing";

describe("terminal route policy", () => {
  it("denies acquisition and private paths", () => {
    for (const path of [
      "/admin",
      "/admin/sentiment-pilot",
      "/batch/trigger",
      "/ops/r2cat",
      "/private/customer-outcomes",
      "/workspace/import",
    ]) {
      expect(isDeniedProductPath(path)).toBe(true);
    }
  });

  it("allows public read-only product APIs", () => {
    for (const [method, path] of [
      ["GET", "/health"],
      ["GET", "/api/status"],
      ["GET", "/api/search"],
      ["GET", "/api/artist-security/mbid::demo"],
      ["GET", "/api/markets"],
      ["GET", "/api/portfolio"],
      ["POST", "/api/underwrite"],
    ]) {
      expect(isPublicDemoPathBlocked(method, path)).toBe(false);
    }
  });

  it("blocks public workspace, import, monitoring, and lineup surfaces", () => {
    for (const [method, path] of [
      ["GET", "/api/shortlist"],
      ["GET", "/api/monitor"],
      ["GET", "/api/decisions"],
      ["GET", "/api/backtest"],
      ["GET", "/api/vault"],
      ["GET", "/api/readiness"],
      ["GET", "/api/pace"],
      ["POST", "/api/shortlist"],
      ["POST", "/api/portfolio/lineup"],
      ["POST", "/api/underwrite/save"],
    ]) {
      expect(isPublicDemoPathBlocked(method, path)).toBe(true);
    }
  });
});
