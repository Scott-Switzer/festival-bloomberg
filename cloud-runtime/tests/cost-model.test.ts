import { describe, it, expect } from "vitest";
import {
  accountRailCost,
  browserMarginalCostUsd,
  costBasisForRail,
  BROWSER_RUN_INCLUDED_HOURS_PER_MONTH,
  BROWSER_RUN_METERED_RATE_PER_HOUR_USD,
  MONID_PAID_PAGE_COST_USD,
} from "../src/cost-model";

describe("cost model — Browser Run is included-allowance/metered, NOT free", () => {
  it("direct HTTP rail is INCLUDED_WORKER_USAGE with zero cash and zero browser time", () => {
    const c = accountRailCost("RAIL_0_DIRECT_HTTP", 0);
    expect(c.cost_basis).toBe("INCLUDED_WORKER_USAGE");
    expect(c.provider_cash_spend_usd).toBe(0);
    expect(c.browser_ms).toBe(0);
    expect(c.estimated_browser_marginal_usd).toBe(0);
  });

  it("browser rails are CLOUDFLARE_BROWSER_INCLUDED_OR_METERED, not FREE_RAIL", () => {
    for (const rail of ["RAIL_1_BROWSER_CONTENT", "RAIL_2_BROWSER_SCRAPE", "RAIL_3_PLAYWRIGHT"]) {
      const c = accountRailCost(rail, 60_000); // 1 browser-minute
      expect(c.cost_basis).toBe("CLOUDFLARE_BROWSER_INCLUDED_OR_METERED");
      // $0.09/hour × 1 minute = $0.0015 estimated marginal cost
      expect(c.estimated_browser_marginal_usd).toBeCloseTo(0.0015, 6);
      expect(c.browser_ms).toBe(60_000);
      expect(c.provider_cash_spend_usd).toBe(0); // no provider cash — but NOT free
    }
  });

  it("Monid rail is MEASURED_PAID_PROVIDER at the accepted $0.0009 rate", () => {
    const c = accountRailCost("RAIL_4_MONID", 0);
    expect(c.cost_basis).toBe("MEASURED_PAID_PROVIDER");
    expect(c.provider_cash_spend_usd).toBe(MONID_PAID_PAGE_COST_USD);
    expect(c.estimated_browser_marginal_usd).toBe(0);
  });

  it("honors the MEASURED provider cost (tinyfish free fetch = $0, context.dev = $0.0009)", () => {
    // tinyfish free path succeeded → measured $0, basis stays MEASURED (not FREE_RAIL)
    const free = accountRailCost("RAIL_4_MONID", 0, 0);
    expect(free.provider_cash_spend_usd).toBe(0);
    expect(free.cost_basis).toBe("MEASURED_PAID_PROVIDER");
    // context.dev HTML fallback ran → measured $0.0009
    const paid = accountRailCost("RAIL_4_MONID", 0, 0.0009);
    expect(paid.provider_cash_spend_usd).toBe(0.0009);
    expect(paid.cost_basis).toBe("MEASURED_PAID_PROVIDER");
    // no measurement → accepted default
    const fallback = accountRailCost("RAIL_4_MONID", 0, null);
    expect(fallback.provider_cash_spend_usd).toBe(MONID_PAID_PAGE_COST_USD);
  });

  it("failed/unknown rails account as NONE", () => {
    const c = accountRailCost("RAIL_UNSUPPORTED", 0);
    expect(c.cost_basis).toBe("NONE");
    expect(c.provider_cash_spend_usd).toBe(0);
  });

  it("browser marginal cost scales linearly with browser ms at $0.09/hour", () => {
    // 1 hour of browser time = $0.09
    expect(browserMarginalCostUsd(3_600_000)).toBeCloseTo(BROWSER_RUN_METERED_RATE_PER_HOUR_USD, 9);
    // 10 included hours/month are allowance; beyond that the same rate applies
    const includedHoursMs = BROWSER_RUN_INCLUDED_HOURS_PER_MONTH * 3_600_000;
    expect(browserMarginalCostUsd(includedHoursMs)).toBeCloseTo(0.9, 9);
    // half an hour = $0.045
    expect(browserMarginalCostUsd(1_800_000)).toBeCloseTo(0.045, 9);
  });

  it("cost basis mapping covers every rail", () => {
    expect(costBasisForRail("RAIL_0_DIRECT_HTTP")).toBe("INCLUDED_WORKER_USAGE");
    expect(costBasisForRail("RAIL_1_BROWSER_CONTENT")).toBe("CLOUDFLARE_BROWSER_INCLUDED_OR_METERED");
    expect(costBasisForRail("RAIL_2_BROWSER_SCRAPE")).toBe("CLOUDFLARE_BROWSER_INCLUDED_OR_METERED");
    expect(costBasisForRail("RAIL_3_PLAYWRIGHT")).toBe("CLOUDFLARE_BROWSER_INCLUDED_OR_METERED");
    expect(costBasisForRail("RAIL_4_MONID")).toBe("MEASURED_PAID_PROVIDER");
    expect(costBasisForRail("RAIL_5_SPECIALIZED")).toBe("NONE");
    expect(costBasisForRail("RAIL_UNSUPPORTED")).toBe("NONE");
  });
});
