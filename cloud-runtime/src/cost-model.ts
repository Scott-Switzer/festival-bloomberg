/**
 * Acquisition cost model.
 *
 * Rails are NOT uniformly free. Correct accounting per rail:
 *
 *   RAIL_0_DIRECT_HTTP     Worker fetch()            → INCLUDED_WORKER_USAGE
 *   RAIL_1_BROWSER_CONTENT Browser Run /content      → CLOUDFLARE_BROWSER_INCLUDED_OR_METERED
 *   RAIL_2_BROWSER_SCRAPE  Browser Run /scrape       → CLOUDFLARE_BROWSER_INCLUDED_OR_METERED
 *   RAIL_3_PLAYWRIGHT      Browser Run Playwright    → CLOUDFLARE_BROWSER_INCLUDED_OR_METERED
 *   RAIL_4_MONID           Monid context.dev         → MEASURED_PAID_PROVIDER
 *
 * Browser Run is NOT free: Workers Paid includes 10 browser-hours/month, then
 * $0.09 per additional browser-hour. Quick Actions consume browser time too.
 * (https://developers.cloudflare.com/browser-run/pricing/)
 *
 * The Governor budget ledger tracks PROVIDER CASH SPEND only (Monid today).
 * Browser time is tracked as allowance usage + estimated marginal cost in the
 * scorecard, so cost/useful-observation never reports $0 falsely as the
 * dataset grows past the monthly browser allowance.
 */

export const BROWSER_RUN_INCLUDED_HOURS_PER_MONTH = 10;
export const BROWSER_RUN_METERED_RATE_PER_HOUR_USD = 0.09;
export const MONID_PAID_PAGE_COST_USD = 0.0009;
export const MS_PER_HOUR = 3_600_000;

export type CostBasis =
  | "INCLUDED_WORKER_USAGE"
  | "CLOUDFLARE_BROWSER_INCLUDED_OR_METERED"
  | "MEASURED_PAID_PROVIDER"
  | "NONE";

export interface RailCostAccount {
  /** Cash spend that must flow through the Governor budget (paid providers only). */
  provider_cash_spend_usd: number;
  cost_basis: CostBasis;
  /** Measured browser execution milliseconds for this rail. */
  browser_ms: number;
  /** Estimated marginal browser cost at the metered rate ($0.09/browser-hour). */
  estimated_browser_marginal_usd: number;
}

/** Estimated marginal browser cost for a given number of browser milliseconds. */
export function browserMarginalCostUsd(browserMs: number): number {
  return (browserMs / MS_PER_HOUR) * BROWSER_RUN_METERED_RATE_PER_HOUR_USD;
}

/** Canonical cost basis for each acquisition rail. */
export function costBasisForRail(rail: string): CostBasis {
  switch (rail) {
    case "RAIL_0_DIRECT_HTTP":
      return "INCLUDED_WORKER_USAGE";
    case "RAIL_1_BROWSER_CONTENT":
    case "RAIL_2_BROWSER_SCRAPE":
    case "RAIL_3_PLAYWRIGHT":
      return "CLOUDFLARE_BROWSER_INCLUDED_OR_METERED";
    case "RAIL_4_MONID":
      return "MEASURED_PAID_PROVIDER";
    default:
      return "NONE";
  }
}

/**
 * Account the full cost of one acquisition result, per rail semantics.
 *
 * For MEASURED_PAID_PROVIDER rails, `measuredProviderCostUsd` is preferred
 * when provided (tinyfish free fetch = $0, context.dev HTML fallback =
 * $0.0009); otherwise the accepted MONID_PAID_PAGE_COST_USD is used.
 */
export function accountRailCost(rail: string, browserMs: number, measuredProviderCostUsd?: number | null): RailCostAccount {
  const basis = costBasisForRail(rail);
  switch (basis) {
    case "MEASURED_PAID_PROVIDER":
      return {
        provider_cash_spend_usd:
          measuredProviderCostUsd != null ? measuredProviderCostUsd : MONID_PAID_PAGE_COST_USD,
        cost_basis: basis,
        browser_ms: 0,
        estimated_browser_marginal_usd: 0,
      };
    case "CLOUDFLARE_BROWSER_INCLUDED_OR_METERED":
      return {
        provider_cash_spend_usd: 0,
        cost_basis: basis,
        browser_ms: browserMs,
        estimated_browser_marginal_usd: browserMarginalCostUsd(browserMs),
      };
    default:
      return {
        provider_cash_spend_usd: 0,
        cost_basis: basis,
        browser_ms: 0,
        estimated_browser_marginal_usd: 0,
      };
  }
}
