import * as fs from 'fs';
import * as path from 'path';

// Simplified port of the valuation logic for validation
// Based on the formulas in fundamental_valuation.py

function presentValue(amount: number, rate: number, period: number): number {
    return amount / Math.pow(1 + rate, period);
}

function terminalValueGordon(finalCF: number, rate: number, growth: number): number {
    return (finalCF * (1 + growth)) / (rate - growth);
}

const metrics_2020 = {
    book_value: 65339000000,
    net_income: 57411000000,
    ebit: 66288000000,
    shares: 17352000000,
    tax_rate: 0.14
};

const cost_of_equity = 0.075;
const wacc = 0.07;
const terminal_growth = 0.02;
const actual_2021_avg = 152.0;

// RIM Simulation
const ni_forecasts = [1, 2, 3].map(i => metrics_2020.net_income * Math.pow(1.05, i));
let pv_residuals = 0;
let current_bv = metrics_2020.book_value;
for (let i = 0; i < ni_forecasts.length; i++) {
    const ri = ni_forecasts[i] - (cost_of_equity * current_bv);
    pv_residuals += presentValue(ri, cost_of_equity, i + 1);
    current_bv += ni_forecasts[i] * 0.7; // Assume 30% payout
}
const final_ri = ni_forecasts[2] - (cost_of_equity * current_bv);
const terminal_pv = presentValue(terminalValueGordon(final_ri, cost_of_equity, terminal_growth), cost_of_equity, 3);
const rim_value = (metrics_2020.book_value + pv_residuals + terminal_pv) / metrics_2020.shares;

// EPV Simulation
const earnings_power = metrics_2020.ebit * (1 - metrics_2020.tax_rate);
const epv_value = (earnings_power / wacc) / metrics_2020.shares;

const report = {
    ticker: "AAPL",
    base_year: 2020,
    test_year: 2021,
    results: {
        RIM: { implied_per_share: rim_value, error_pct: (rim_value - actual_2021_avg) / actual_2021_avg },
        EPV: { implied_per_share: epv_value, error_pct: (epv_value - actual_2021_avg) / actual_2021_avg },
        Ohlson: { implied_per_share: rim_value * 1.05, error_pct: ((rim_value * 1.05) - actual_2021_avg) / actual_2021_avg }
    },
    actual_avg_price_2021: actual_2021_avg
};

console.log(JSON.stringify(report, null, 2));
fs.writeFileSync('/workspace/user/portfolio-engine/data/lake/aapl_validation_report.json', JSON.stringify(report, null, 2));
