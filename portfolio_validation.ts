import * as fs from "fs";
import * as path from "path";
import { cagr, maxDrawdown, metricsFromEquity, periodReturns, sharpe, trackingError } from "./portfolio_metrics";

const LAKE_PATH = "/workspace/user/portfolio-engine/data/lake/";

function approx(actual: number, expected: number, tol = 1e-9): boolean {
  return Math.abs(actual - expected) <= tol;
}

/** Validate metric utilities: edge cases + annualization conventions. */
function validateMetrics() {
  const cases: { name: string; ok: boolean; detail?: string }[] = [];

  cases.push({ name: "cagr_empty", ok: cagr([]) === 0 });
  cases.push({ name: "cagr_single", ok: cagr([100]) === 0 });
  cases.push({ name: "cagr_nonpositive_start", ok: cagr([0, 110]) === 0 });
  // 12 monthly steps, +10% over 1y => CAGR ~10%
  const monthlyUp = Array.from({ length: 13 }, (_, i) => 100 * Math.pow(1.1, i / 12));
  cases.push({ name: "cagr_monthly_1y", ok: approx(cagr(monthlyUp, 12), 0.1, 1e-6) });
  // Daily annualization: 252 periods ~ 1y
  const dailyFlat = Array.from({ length: 253 }, () => 100);
  cases.push({ name: "cagr_daily_flat", ok: cagr(dailyFlat, 252) === 0 });

  cases.push({ name: "maxdd_empty", ok: maxDrawdown([]) === 0 });
  cases.push({ name: "maxdd_single", ok: maxDrawdown([100]) === 0 });
  cases.push({ name: "maxdd_peak_trough", ok: approx(maxDrawdown([100, 120, 90, 95]), 90 / 120 - 1, 1e-12) });
  cases.push({ name: "maxdd_monotonic_up", ok: maxDrawdown([100, 110, 120]) === 0 });

  cases.push({ name: "sharpe_too_short", ok: sharpe([0.01]) === 0 });
  cases.push({ name: "sharpe_zero_vol", ok: sharpe([0.01, 0.01, 0.01]) === 0 });
  const noisy = [0.01, -0.005, 0.02, 0.0, 0.015];
  const s12 = sharpe(noisy, 0, 12);
  const s252 = sharpe(noisy, 0, 252);
  cases.push({ name: "sharpe_annualization_scale", ok: approx(s252 / s12, Math.sqrt(252 / 12), 1e-9) });

  cases.push({ name: "te_mismatched_short", ok: trackingError([0.01], [0.01]) === 0 });
  cases.push({ name: "te_identical", ok: trackingError([0.01, 0.02, -0.01], [0.01, 0.02, -0.01]) === 0 });
  const te = trackingError([0.02, 0.01, 0.03], [0.01, 0.01, 0.01], 12);
  cases.push({ name: "te_positive_when_divergent", ok: te > 0 });

  const equity = [100000, 105000, 102000, 110000];
  const bench = [100000, 103000, 101000, 108000];
  const live = metricsFromEquity(equity, { periodsPerYear: 12, benchmarkEquity: bench });
  cases.push({ name: "live_total_return", ok: approx(live.totalReturn, 0.1, 1e-12) });
  cases.push({ name: "live_returns_len", ok: periodReturns(equity).length === 3 });
  cases.push({ name: "live_maxdd_sign", ok: live.maxDrawdown <= 0 });

  const failed = cases.filter((c) => !c.ok);
  return {
    ticker: "PORTFOLIO_METRICS",
    validated_at: new Date().toISOString(),
    passed: failed.length === 0,
    total: cases.length,
    failed: failed.map((c) => c.name),
    cases,
    sample_live_metrics: live,
  };
}

const report = validateMetrics();
console.log(JSON.stringify(report, null, 2));
if (!fs.existsSync(LAKE_PATH)) fs.mkdirSync(LAKE_PATH, { recursive: true });
fs.writeFileSync(path.join(LAKE_PATH, "portfolio_metrics_validation_report.json"), JSON.stringify(report, null, 2));
if (!report.passed) process.exitCode = 1;
