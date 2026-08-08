import math
from dataclasses import dataclass
from decimal import Decimal
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# Import the patched logic (simulated here for execution)
def simulate_monthly_rebalance(
    close_prices: pd.DataFrame,
    dividends: pd.DataFrame,
    symbol_weights: dict[str, float],
    initial_capital: float = 100000.0,
) -> tuple[pd.Series, pd.Series]:
    prices = close_prices[list(symbol_weights)].dropna(how="all").ffill().dropna()
    dividend_frame = dividends.reindex(prices.index).fillna(0.0)
    weights = pd.Series(symbol_weights, dtype=float)
    weights = weights / weights.sum()
    
    equity_values: list[float] = []
    equity_dates: list[pd.Timestamp] = []
    monthly_cash: dict[pd.Period, float] = {}
    
    current_portfolio_value = float(initial_capital)
    cash_holdings = 0.0
    current_units = pd.Series(0.0, index=prices.columns)

    for month, group in prices.groupby(prices.index.to_period("M")):
        if group.empty:
            continue
        
        start_prices = group.iloc[0].replace(0, math.nan).dropna()
        active_weights = weights.reindex(start_prices.index).dropna()
        active_weights = active_weights / active_weights.sum()
        
        current_units = (current_portfolio_value * active_weights / start_prices).fillna(0.0)
        cash_holdings = 0.0
        
        for current_date, price_row in group.iterrows():
            day_prices = price_row.reindex(current_units.index)
            market_value = float((current_units * day_prices).sum())
            
            day_dividends = dividend_frame.loc[current_date].reindex(current_units.index).fillna(0.0)
            day_cash_received = float((current_units * day_dividends).sum())
            
            cash_holdings += day_cash_received
            monthly_cash[month] = monthly_cash.get(month, 0.0) + day_cash_received
            current_portfolio_value = market_value + cash_holdings
            
            equity_dates.append(pd.Timestamp(current_date))
            equity_values.append(current_portfolio_value)

    equity = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates), name="equity")
    cash_flow = pd.Series(monthly_cash, dtype=float, name="monthly_cash_flow")
    return equity, cash_flow

def calculate_backtest_metrics(equity_curve, monthly_cash_flow, initial_capital):
    daily_returns = equity_curve.pct_change().dropna()
    total_return = float(equity_curve.iloc[-1] / initial_capital - 1.0)
    days = max((equity_curve.index[-1] - equity_curve.index[0]).days, 1)
    years = days / 365.25
    cagr = float((equity_curve.iloc[-1] / initial_capital) ** (1 / years) - 1.0)
    drawdown = equity_curve / equity_curve.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    sharpe = float(daily_returns.mean() / daily_returns.std() * math.sqrt(252)) if not daily_returns.empty else 0.0
    monthly_cash = float(monthly_cash_flow.mean()) if not monthly_cash_flow.empty else 0.0
    annual_cash_yield = float(monthly_cash * 12 / initial_capital)
    
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "monthly_cash_flow": monthly_cash,
        "annual_cash_yield": annual_cash_yield,
        "ending_value": float(equity_curve.iloc[-1])
    }

def run_backtest():
    tickers = ["SPY", "BIL", "PFF", "VTV", "QQQ"]
    weights = {"SPY": 0.2, "BIL": 0.2, "PFF": 0.2, "VTV": 0.2, "QQQ": 0.2}
    initial_capital = 100000.0
    
    print(f"Downloading data for {tickers}...")
    data = yf.download(tickers, start="2021-06-01", end="2026-05-29", progress=False)
    
    # Handle Adj Close and Dividends
    close_prices = data['Adj Close'].ffill().dropna()
    # Note: yfinance download doesn't return Dividends by default in this format
    # For a quick verification, we'll fetch dividends separately or assume zero if needed
    # But let's try to get them
    
    divs_list = []
    for t in tickers:
        ticker_obj = yf.Ticker(t)
        d = ticker_obj.dividends
        d.name = t
        divs_list.append(d)
    
    dividends = pd.concat(divs_list, axis=1).reindex(close_prices.index).fillna(0.0)
    
    print("Running simulation...")
    equity, cash_flow = simulate_monthly_rebalance(close_prices, dividends, weights, initial_capital)
    metrics = calculate_backtest_metrics(equity, cash_flow, initial_capital)
    
    print("\n--- Patched Backtest Results ---")
    for k, v in metrics.items():
        if "return" in k or "cagr" in k or "drawdown" in k or "yield" in k:
            print(f"{k}: {v:.2%}")
        elif "value" in k or "flow" in k:
            print(f"{k}: ${v:,.2f}")
        else:
            print(f"{k}: {v:.2f}")

if __name__ == "__main__":
    run_backtest()
