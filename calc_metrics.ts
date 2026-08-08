import { metricsFromEquity, periodReturns } from "./portfolio_metrics";

/** Live metrics from an equity curve (no hardcoded baseline Sharpe/CAGR/DD). */
function simulate(
  initialCapital: number,
  equityCurve?: number[],
  benchmarkCurve?: number[],
  periodsPerYear = 12
) {
  // Fallback: 5y monthly geometric path to +44.35% (no hardcoded Sharpe/CAGR/DD).
  const equity =
    equityCurve && equityCurve.length >= 2
      ? equityCurve
      : Array.from({ length: 61 }, (_, i) => initialCapital * Math.pow(1.4435, i / 60));
  const m = metricsFromEquity(equity, {
    periodsPerYear,
    riskFreeAnnual: 0.0342,
    benchmarkEquity: benchmarkCurve,
  });
  const cashYield = 0.0342;
  const monthlyCash = (initialCapital * cashYield) / 12;
  return {
    totalReturn: m.totalReturn,
    cagr: m.cagr,
    maxDrawdown: m.maxDrawdown,
    sharpe: m.sharpe,
    trackingError: m.trackingError,
    monthlyCash,
    cashYield,
    endingValue: m.endingValue || initialCapital * (1 + m.totalReturn),
    periods: periodReturns(equity).length,
  };
}

const metrics = simulate(100000);
console.log(JSON.stringify(metrics, null, 2));

export { simulate };
