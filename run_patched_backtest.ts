import * as fs from "fs";
import * as path from "path";
import { metricsFromEquity } from "./portfolio_metrics";
import { simulate } from "./calc_metrics";

const LAKE_PATH = "/workspace/user/portfolio-engine/data/lake/";

function loadHistoryEquity(): number[] | null {
  const p = path.join(LAKE_PATH, "backtest_results.json");
  if (!fs.existsSync(p)) return null;
  try {
    const raw = JSON.parse(fs.readFileSync(p, "utf8"));
    const history = Array.isArray(raw) ? raw : raw.history;
    if (!Array.isArray(history) || history.length < 2) return null;
    return history.map((h: { totalValue: number }) => Number(h.totalValue)).filter((v: number) => isFinite(v));
  } catch {
    return null;
  }
}

async function run() {
  try {
    console.log("Starting backtest with patched logic...");
    const equity = loadHistoryEquity();
    const initialCapital = equity?.[0] ?? 100000;
    const metrics = equity
      ? metricsFromEquity(equity, { periodsPerYear: 12, riskFreeAnnual: 0.0342 })
      : simulate(initialCapital);
    const outcome = {
      meta: {
        generated_at: new Date().toISOString(),
        source: equity ? "backtest_results.json" : "simulate_fallback",
        initial_capital: initialCapital,
        patched: true,
      },
      metrics,
    };
    if (!fs.existsSync(LAKE_PATH)) fs.mkdirSync(LAKE_PATH, { recursive: true });
    const out = path.join(LAKE_PATH, "patched_backtest_outcome.json");
    fs.writeFileSync(out, JSON.stringify(outcome, null, 2));
    console.log(JSON.stringify(outcome, null, 2));
    console.log(`Structured outcome saved to ${out}`);
  } catch (e) {
    console.error(e);
    process.exitCode = 1;
  }
}

run();
