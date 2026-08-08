import sys
import os
import pandas as pd
import json
from datetime import datetime

# Path setup to import local modules
sys.path.append('/workspace/user/portfolio-engine')

from models.fundamental_valuation import (
    FundamentalValuationEngine, RIMInput, EPVInput, OhlsonInput
)

def run_aapl_validation():
    print("Starting AAPL 2020/2021 Validation Test...")
    
    # 2020 Actual Fundamentals for AAPL (Approximate from Lake context)
    # BV: ~$65B, NI: ~$57B, EBIT: ~$66B, Shares: ~17B
    metrics_2020 = {
        "book_value": 65339000000,
        "net_income": 57411000000,
        "ebit": 66288000000,
        "shares": 17352000000,
        "tax_rate": 0.14
    }
    
    # Assumptions for 2021 forecasts
    growth_rates = [0.05, 0.05, 0.05]
    cost_of_equity = 0.075
    wacc = 0.07
    terminal_growth = 0.02
    
    engine = FundamentalValuationEngine()
    
    # 1. RIM Calculation
    rim_in = RIMInput(
        beginning_book_value=metrics_2020["book_value"],
        net_income_forecasts=[metrics_2020["net_income"] * (1+g) for g in growth_rates],
        cost_of_equity=cost_of_equity,
        terminal_growth_rate=terminal_growth,
        shares_outstanding=metrics_2020["shares"]
    )
    res_rim = engine.rim(rim_in)
    
    # 2. EPV Calculation
    epv_in = EPVInput(
        normalized_ebit=metrics_2020["ebit"],
        tax_rate=metrics_2020["tax_rate"],
        wacc=wacc,
        shares_outstanding=metrics_2020["shares"]
    )
    res_epv = engine.epv(epv_in)
    
    # 3. Ohlson Calculation
    ohlson_in = OhlsonInput(
        beginning_book_value=metrics_2020["book_value"],
        net_income_forecasts=[metrics_2020["net_income"] * (1+g) for g in growth_rates],
        cost_of_equity=cost_of_equity,
        terminal_growth_rate=terminal_growth,
        shares_outstanding=metrics_2020["shares"]
    )
    res_ohlson = engine.ohlson(ohlson_in)
    
    # Actual Price Comparison (AAPL Jan 2021 was ~$130, Dec 2021 ~$175)
    # Average price ~150
    actual_2021_avg = 152.0
    
    report = {
        "ticker": "AAPL",
        "base_year": 2020,
        "test_year": 2021,
        "results": {
            "RIM": {
                "implied_per_share": res_rim.per_share_value,
                "error_pct": (res_rim.per_share_value - actual_2021_avg) / actual_2021_avg
            },
            "EPV": {
                "implied_per_share": res_epv.per_share_value,
                "error_pct": (res_epv.per_share_value - actual_2021_avg) / actual_2021_avg
            },
            "Ohlson": {
                "implied_per_share": res_ohlson.per_share_value,
                "error_pct": (res_ohlson.per_share_value - actual_2021_avg) / actual_2021_avg
            }
        },
        "actual_avg_price_2021": actual_2021_avg
    }
    
    print(json.dumps(report, indent=2))
    return report

if __name__ == "__main__":
    report = run_aapl_validation()
    with open('/workspace/user/portfolio-engine/data/lake/aapl_validation_report.json', 'w') as f:
        json.dump(report, f, indent=2)
