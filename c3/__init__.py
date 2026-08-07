"""
C3 festival integration layer for Festival Bloomberg
Implements C3 festival portfolio registry and format-specific parsers
"""
from .c3_portfolio import C3PortfolioRegistry, C3Festival, FestivalFormat, ProductionRole, Currency
from .format_parsers import (
    BaseLineupParser,
    PosterGridParser,
    DayStageScheduleParser,
    MultiWeekendParser,
    GenreCuratedGridParser,
    SimpleListParser,
    ParserFactory,
    ParsedArtist,
    LineupParseResult,
    parse_lineup
)

__all__ = [
    'C3PortfolioRegistry',
    'C3Festival',
    'FestivalFormat',
    'ProductionRole',
    'Currency',
    'BaseLineupParser',
    'PosterGridParser',
    'DayStageScheduleParser',
    'MultiWeekendParser',
    'GenreCuratedGridParser',
    'SimpleListParser',
    'ParserFactory',
    'ParsedArtist',
    'LineupParseResult',
    'parse_lineup'
]
