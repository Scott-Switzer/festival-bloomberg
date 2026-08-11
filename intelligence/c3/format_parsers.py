"""
Format-Specific Lineup Parsers for C3 Festivals
Implements parsers for different festival lineup formats per Festival Bloomberg spec
"""
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, date
from enum import Enum
from dataclasses import dataclass, field
import re
import json
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ParsedArtist:
    """Parsed artist from lineup"""
    def __init__(self, name: str, stage: Optional[str] = None, day: Optional[str] = None, 
                 position: Optional[str] = None, weekend: Optional[int] = None):
        self.name = name
        self.stage = stage
        self.day = day
        self.position = position  # headliner, sub-headliner, supporting
        self.weekend = weekend
        self.metadata = {}


class LineupParseResult:
    """Result of lineup parsing"""
    def __init__(self, artists: List[ParsedArtist], format_profile: str, 
                 confidence: float, metadata: Dict[str, Any] = None):
        self.artists = artists
        self.format_profile = format_profile
        self.confidence = confidence
        self.metadata = metadata or {}
        self.errors = []
        self.warnings = []


class BaseLineupParser(ABC):
    """Base class for lineup parsers"""
    
    def __init__(self, festival_id: str, year: int):
        self.festival_id = festival_id
        self.year = year
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
    
    @abstractmethod
    def parse(self, content: str, content_type: str = "text") -> LineupParseResult:
        """Parse lineup content"""
        pass
    
    def _normalize_artist_name(self, name: str) -> str:
        """Normalize artist name"""
        return name.strip().title()
    
    def _determine_position(self, index: int, total: int) -> str:
        """Determine artist position based on index"""
        if index < total * 0.1:  # Top 10%
            return "headliner"
        elif index < total * 0.3:  # Top 30%
            return "sub-headliner"
        else:
            return "supporting"


class PosterGridParser(BaseLineupParser):
    """
    Parser for poster grid format (OCR from images)
    Used by Lollapalooza Chicago and ACL
    """
    
    def parse(self, content: str, content_type: str = "text") -> LineupParseResult:
        """
        Parse poster grid content
        
        Args:
            content: OCR text from poster image
            content_type: Content type (text, json, etc.)
            
        Returns:
            LineupParseResult
        """
        artists = []
        
        # Split by common delimiters
        lines = content.split('\n')
        
        # Filter out empty lines and common noise
        lines = [line.strip() for line in lines if line.strip()]
        lines = [line for line in lines if len(line) > 2]  # Filter very short lines
        
        # Remove common poster text
        noise_words = ['festival', 'presents', 'music', 'arts', 'lineup', 'poster', 
                      'weekend', 'day', 'stage', 'featuring', 'and', 'with', 'special']
        lines = [line for line in lines if not any(noise in line.lower() for noise in noise_words)]
        
        # Parse artist names (assume larger text = more prominent)
        for i, line in enumerate(lines):
            if self._is_valid_artist_name(line):
                artist = ParsedArtist(
                    name=self._normalize_artist_name(line),
                    position=self._determine_position(i, len(lines))
                )
                artists.append(artist)
        
        result = LineupParseResult(
            artists=artists,
            format_profile="poster_grid",
            confidence=0.7,  # OCR has lower confidence
            metadata={
                "total_lines": len(lines),
                "filtered_lines": len(artists),
                "ocr_confidence": "medium"
            }
        )
        
        self.logger.info(f"Parsed {len(artists)} artists from poster grid")
        return result
    
    def _is_valid_artist_name(self, text: str) -> bool:
        """Check if text looks like an artist name"""
        # Artist names typically don't contain numbers (except maybe years)
        # and are usually 2-50 characters
        if len(text) < 2 or len(text) > 50:
            return False
        
        # Should not be all caps (likely header)
        if text.isupper() and len(text) > 5:
            return False
        
        # Should not be all numbers
        if text.isdigit():
            return False
        
        # Should contain at least one letter
        if not any(c.isalpha() for c in text):
            return False
        
        return True


class DayStageScheduleParser(BaseLineupParser):
    """
    Parser for day/stage schedule format (structured JSON)
    Used by international Lollapalooza editions
    """
    
    def parse(self, content: str, content_type: str = "json") -> LineupParseResult:
        """
        Parse day/stage schedule content
        
        Args:
            content: JSON schedule data
            content_type: Content type
            
        Returns:
            LineupParseResult
        """
        artists = []
        
        try:
            if content_type == "json":
                data = json.loads(content)
            else:
                # Try to parse as JSON anyway
                data = json.loads(content)
            
            # Parse schedule structure
            # Expected format: { "days": [{ "date": "...", "stages": [{ "name": "...", "artists": [...] }] }] }
            
            if "days" in data:
                for day_data in data["days"]:
                    day = day_data.get("date", day_data.get("day", ""))
                    
                    if "stages" in day_data:
                        for stage_data in day_data["stages"]:
                            stage = stage_data.get("name", "")
                            
                            if "artists" in stage_data:
                                for artist_data in stage_data["artists"]:
                                    artist_name = artist_data if isinstance(artist_data, str) else artist_data.get("name", "")
                                    
                                    if artist_name:
                                        artist = ParsedArtist(
                                            name=self._normalize_artist_name(artist_name),
                                            stage=stage,
                                            day=day,
                                            position=artist_data.get("position") if isinstance(artist_data, dict) else None
                                        )
                                        artists.append(artist)
            
            # Alternative format: flat artist list with metadata
            elif "artists" in data:
                for artist_data in data["artists"]:
                    artist_name = artist_data if isinstance(artist_data, str) else artist_data.get("name", "")
                    
                    if artist_name:
                        artist = ParsedArtist(
                            name=self._normalize_artist_name(artist_name),
                            stage=artist_data.get("stage") if isinstance(artist_data, dict) else None,
                            day=artist_data.get("day") if isinstance(artist_data, dict) else None,
                            position=artist_data.get("position") if isinstance(artist_data, dict) else None
                        )
                        artists.append(artist)
            
            result = LineupParseResult(
                artists=artists,
                format_profile="day_stage_schedule",
                confidence=0.95,  # Structured data has high confidence
                metadata={
                    "total_artists": len(artists),
                    "unique_stages": len(set(a.stage for a in artists if a.stage)),
                    "unique_days": len(set(a.day for a in artists if a.day))
                }
            )
            
            self.logger.info(f"Parsed {len(artists)} artists from schedule JSON")
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}")
            return LineupParseResult(
                artists=[],
                format_profile="day_stage_schedule",
                confidence=0.0,
                metadata={"error": str(e)}
            )
        except Exception as e:
            self.logger.error(f"Failed to parse schedule: {e}")
            return LineupParseResult(
                artists=[],
                format_profile="day_stage_schedule",
                confidence=0.0,
                metadata={"error": str(e)}
            )


class MultiWeekendParser(BaseLineupParser):
    """
    Parser for multi-weekend format
    Used by festivals with multiple weekend editions
    """
    
    def parse(self, content: str, content_type: str = "json") -> LineupParseResult:
        """
        Parse multi-weekend content
        
        Args:
            content: JSON or text content
            content_type: Content type
            
        Returns:
            LineupParseResult
        """
        artists = []
        
        try:
            if content_type == "json":
                data = json.loads(content)
            else:
                data = json.loads(content)
            
            # Expected format: { "weekends": [{ "number": 1, "artists": [...] }] }
            
            if "weekends" in data:
                for weekend_data in data["weekends"]:
                    weekend_num = weekend_data.get("number", 1)
                    
                    if "artists" in weekend_data:
                        for artist_data in weekend_data["artists"]:
                            artist_name = artist_data if isinstance(artist_data, str) else artist_data.get("name", "")
                            
                            if artist_name:
                                artist = ParsedArtist(
                                    name=self._normalize_artist_name(artist_name),
                                    weekend=weekend_num,
                                    position=artist_data.get("position") if isinstance(artist_data, dict) else None
                                )
                                artists.append(artist)
            
            result = LineupParseResult(
                artists=artists,
                format_profile="multi_weekend",
                confidence=0.9,
                metadata={
                    "total_artists": len(artists),
                    "weekend_count": len(set(a.weekend for a in artists if a.weekend))
                }
            )
            
            self.logger.info(f"Parsed {len(artists)} artists from multi-weekend format")
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}")
            return LineupParseResult(
                artists=[],
                format_profile="multi_weekend",
                confidence=0.0,
                metadata={"error": str(e)}
            )
        except Exception as e:
            self.logger.error(f"Failed to parse multi-weekend: {e}")
            return LineupParseResult(
                artists=[],
                format_profile="multi_weekend",
                confidence=0.0,
                metadata={"error": str(e)}
            )


class GenreCuratedGridParser(BaseLineupParser):
    """
    Parser for genre-curated grid format
    Used by festivals with genre-based stage organization
    """
    
    def parse(self, content: str, content_type: str = "json") -> LineupParseResult:
        """
        Parse genre-curated grid content
        
        Args:
            content: JSON or text content
            content_type: Content type
            
        Returns:
            LineupParseResult
        """
        artists = []
        
        try:
            if content_type == "json":
                data = json.loads(content)
            else:
                data = json.loads(content)
            
            # Expected format: { "genres": [{ "name": "Rock", "artists": [...] }] }
            
            if "genres" in data:
                for genre_data in data["genres"]:
                    genre = genre_data.get("name", "")
                    
                    if "artists" in genre_data:
                        for artist_data in genre_data["artists"]:
                            artist_name = artist_data if isinstance(artist_data, str) else artist_data.get("name", "")
                            
                            if artist_name:
                                artist = ParsedArtist(
                                    name=self._normalize_artist_name(artist_name),
                                    position=artist_data.get("position") if isinstance(artist_data, dict) else None
                                )
                                artist.metadata["genre"] = genre
                                artists.append(artist)
            
            result = LineupParseResult(
                artists=artists,
                format_profile="genre_curated_grid",
                confidence=0.85,
                metadata={
                    "total_artists": len(artists),
                    "genre_count": len(set(a.metadata.get("genre") for a in artists if a.metadata.get("genre")))
                }
            )
            
            self.logger.info(f"Parsed {len(artists)} artists from genre-curated grid")
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}")
            return LineupParseResult(
                artists=[],
                format_profile="genre_curated_grid",
                confidence=0.0,
                metadata={"error": str(e)}
            )
        except Exception as e:
            self.logger.error(f"Failed to parse genre grid: {e}")
            return LineupParseResult(
                artists=[],
                format_profile="genre_curated_grid",
                confidence=0.0,
                metadata={"error": str(e)}
            )


class SimpleListParser(BaseLineupParser):
    """
    Parser for simple list format
    Fallback parser for basic artist lists
    """
    
    def parse(self, content: str, content_type: str = "text") -> LineupParseResult:
        """
        Parse simple list content
        
        Args:
            content: Text content with artist names
            content_type: Content type
            
        Returns:
            LineupParseResult
        """
        artists = []
        
        # Split by common delimiters
        lines = content.split('\n')
        
        # Also try comma separation
        if ',' in content and '\n' not in content:
            lines = content.split(',')
        
        # Filter and normalize
        for line in lines:
            line = line.strip()
            if line and len(line) > 2:
                # Remove common prefixes/suffixes
                line = re.sub(r'^[\d\.\-\)]+\s*', '', line)  # Remove numbering
                line = re.sub(r'\s*[\(\[].*?[\)\]]$', '', line)  # Remove parenthetical
                
                if self._is_valid_artist_name(line):
                    artist = ParsedArtist(
                        name=self._normalize_artist_name(line),
                        position=self._determine_position(len(artists), len(lines))
                    )
                    artists.append(artist)
        
        result = LineupParseResult(
            artists=artists,
            format_profile="simple_list",
            confidence=0.6,  # Simple parsing has moderate confidence
            metadata={
                "total_artists": len(artists),
                "total_lines": len(lines)
            }
        )
        
        self.logger.info(f"Parsed {len(artists)} artists from simple list")
        return result
    
    def _is_valid_artist_name(self, text: str) -> bool:
        """Check if text looks like an artist name"""
        if len(text) < 2 or len(text) > 50:
            return False
        
        # Should contain at least one letter
        if not any(c.isalpha() for c in text):
            return False
        
        # Should not be all numbers
        if text.isdigit():
            return False
        
        return True


class ParserFactory:
    """Factory for creating lineup parsers"""
    
    @staticmethod
    def create_parser(format_profile: str, festival_id: str, year: int) -> BaseLineupParser:
        """
        Create appropriate parser for format profile
        
        Args:
            format_profile: Format profile string
            festival_id: Festival ID
            year: Festival year
            
        Returns:
            Lineup parser instance
        """
        from .c3_portfolio import FestivalFormat
        
        format_enum = FestivalFormat(format_profile)
        
        parsers = {
            FestivalFormat.POSTER_GRID: PosterGridParser,
            FestivalFormat.DAY_STAGE_SCHEDULE: DayStageScheduleParser,
            FestivalFormat.MULTI_WEEKEND: MultiWeekendParser,
            FestivalFormat.GENRE_CURATED_GRID: GenreCuratedGridParser,
            FestivalFormat.SIMPLE_LIST: SimpleListParser,
            FestivalFormat.UNKNOWN: SimpleListParser
        }
        
        parser_class = parsers.get(format_enum, SimpleListParser)
        return parser_class(festival_id, year)


def parse_lineup(content: str, format_profile: str, festival_id: str, year: int, 
                content_type: str = "text") -> LineupParseResult:
    """
    Convenience function to parse lineup
    
    Args:
        content: Lineup content
        format_profile: Format profile
        festival_id: Festival ID
        year: Festival year
        content_type: Content type
        
    Returns:
        LineupParseResult
    """
    parser = ParserFactory.create_parser(format_profile, festival_id, year)
    return parser.parse(content, content_type)
