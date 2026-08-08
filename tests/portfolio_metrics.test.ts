import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { cagr, maxDrawdown, metricsFromEquity, periodReturns, sharpe, trackingError } from "../portfolio_metrics";

describe("portfolio_metrics", () => {
  it("handles empty/single equity edge cases", () => {
    assert.equal(cagr([]), 0);
    assert.equal(cagr([100]), 0);
    assert.equal(maxDrawdown([100]), 0);
    assert.equal(sharpe([]), 0);
    assert.equal(trackingError([0.01], [0.01]), 0);
  });

  it("returns zero Sharpe for near-constant returns (float dust)", () => {
    const equity = Array.from({ length: 61 }, (_, i) => 100 * Math.pow(1.4435, i / 60));
    const rets = periodReturns(equity);
    assert.equal(sharpe(rets, 0.0342 / 12, 12), 0);
  });

  it("annualizes CAGR for monthly series over 1y", () => {
    const equity = Array.from({ length: 13 }, (_, i) => 100 * Math.pow(1.1, i / 12));
    assert.ok(Math.abs(cagr(equity, 12) - 0.1) < 1e-6);
  });

  it("computes signed max drawdown", () => {
    assert.ok(Math.abs(maxDrawdown([100, 120, 90]) - (90 / 120 - 1)) < 1e-12);
  });

  it("scales Sharpe by sqrt(periodsPerYear)", () => {
    const rets = [0.01, -0.004, 0.02, 0.003];
    const ratio = sharpe(rets, 0, 252) / sharpe(rets, 0, 12);
    assert.ok(Math.abs(ratio - Math.sqrt(252 / 12)) < 1e-9);
  });

  it("returns zero tracking error for identical series", () => {
    const r = periodReturns([100, 101, 102, 103]);
    assert.equal(trackingError(r, r, 12), 0);
  });

  it("metricsFromEquity total return and drawdown sign", () => {
    const m = metricsFromEquity([100, 110, 105], { periodsPerYear: 12 });
    assert.ok(Math.abs(m.totalReturn - 0.05) < 1e-12);
    assert.ok(m.maxDrawdown <= 0);
  });
});
