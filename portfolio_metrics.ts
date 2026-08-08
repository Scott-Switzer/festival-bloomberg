/** Small pure TS portfolio metrics (no external finance deps). */

export function periodReturns(values: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const prev = values[i - 1];
    if (!(prev > 0) || !isFinite(values[i])) continue;
    out.push(values[i] / prev - 1);
  }
  return out;
}

/** CAGR from equity curve; periodsPerYear annualizes sample frequency (12=monthly, 252=daily). */
export function cagr(values: number[], periodsPerYear = 12): number {
  if (values.length < 2 || !(values[0] > 0) || !(values[values.length - 1] > 0)) return 0;
  const years = (values.length - 1) / periodsPerYear;
  if (!(years > 0)) return 0;
  return Math.pow(values[values.length - 1] / values[0], 1 / years) - 1;
}

/** Max drawdown as a non-positive fraction (e.g. -0.18). */
export function maxDrawdown(values: number[]): number {
  if (values.length < 2) return 0;
  let peak = values[0];
  let worst = 0;
  for (const v of values) {
    if (!isFinite(v)) continue;
    if (v > peak) peak = v;
    if (peak > 0) {
      const dd = v / peak - 1;
      if (dd < worst) worst = dd;
    }
  }
  return worst;
}

/** Annualized Sharpe from period returns. riskFreePeriod is per-period RF (not annual). */
export function sharpe(returns: number[], riskFreePeriod = 0, periodsPerYear = 12): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  let varSum = 0;
  for (const r of returns) varSum += (r - mean) ** 2;
  const std = Math.sqrt(varSum / (returns.length - 1));
  // Treat numerical dust as zero-vol (avoids exploding Sharpe on flat/geometric paths).
  if (!(std > 1e-12)) return 0;
  return ((mean - riskFreePeriod) / std) * Math.sqrt(periodsPerYear);
}

/** Annualized tracking error vs benchmark (sample stdev of excess returns). */
export function trackingError(
  portfolioReturns: number[],
  benchmarkReturns: number[],
  periodsPerYear = 12
): number {
  const n = Math.min(portfolioReturns.length, benchmarkReturns.length);
  if (n < 2) return 0;
  const excess: number[] = [];
  for (let i = 0; i < n; i++) excess.push(portfolioReturns[i] - benchmarkReturns[i]);
  const mean = excess.reduce((a, b) => a + b, 0) / n;
  let varSum = 0;
  for (const e of excess) varSum += (e - mean) ** 2;
  const std = Math.sqrt(varSum / (n - 1));
  if (!(std > 1e-12)) return 0;
  return std * Math.sqrt(periodsPerYear);
}

export function metricsFromEquity(
  equity: number[],
  opts: { periodsPerYear?: number; riskFreeAnnual?: number; benchmarkEquity?: number[] } = {}
) {
  const periodsPerYear = opts.periodsPerYear ?? 12;
  const rfAnnual = opts.riskFreeAnnual ?? 0;
  const rets = periodReturns(equity);
  const rfPeriod = periodsPerYear > 0 ? rfAnnual / periodsPerYear : 0;
  const benchRets = opts.benchmarkEquity ? periodReturns(opts.benchmarkEquity) : [];
  return {
    totalReturn: equity.length >= 2 && equity[0] > 0 ? equity[equity.length - 1] / equity[0] - 1 : 0,
    cagr: cagr(equity, periodsPerYear),
    maxDrawdown: maxDrawdown(equity),
    sharpe: sharpe(rets, rfPeriod, periodsPerYear),
    trackingError: benchRets.length ? trackingError(rets, benchRets, periodsPerYear) : 0,
    endingValue: equity.length ? equity[equity.length - 1] : 0,
  };
}
