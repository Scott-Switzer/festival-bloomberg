"""
Warehouse layer for Festival Bloomberg
Implements DuckDB analytical warehouse integration
"""
from .duckdb_manager import DuckDBWarehouse, create_warehouse

__all__ = ['DuckDBWarehouse', 'create_warehouse']
