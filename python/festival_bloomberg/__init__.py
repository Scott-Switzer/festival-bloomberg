"""Festival Bloomberg Python utilities: DuckDB warehouse paths and VADER sentiment."""

from .paths import (
    DEFAULT_WAREHOUSE_PATH,
    WAREHOUSE_ENV_VAR,
    resolve_warehouse_path,
)
from .vader_sentiment import SentimentScore, classify_compound, score_texts, score_text
from .duckdb_warehouse import DuckDbWarehouse, open_warehouse

__all__ = [
    "DEFAULT_WAREHOUSE_PATH",
    "WAREHOUSE_ENV_VAR",
    "resolve_warehouse_path",
    "SentimentScore",
    "classify_compound",
    "score_text",
    "score_texts",
    "DuckDbWarehouse",
    "open_warehouse",
]
