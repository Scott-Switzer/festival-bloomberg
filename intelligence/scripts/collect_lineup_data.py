#!/usr/bin/env python3
"""
Data collection script for Festival Intelligence Terminal.
Collects festival lineup data from public sources.
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contracts.festivals import INITIAL_FESTIVALS
import polars as pl


def collect_festival_data():
    """Collect and save festival data."""
    print("Collecting festival data...")
    
    # Create warehouse directories
    warehouse_dir = project_root / "warehouse" / "raw"
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert to DataFrame and save
    festivals_df = pl.DataFrame(INITIAL_FESTIVALS)
    output_path = warehouse_dir / "festivals.parquet"
    festivals_df.write_parquet(output_path)
    
    print(f"Saved {len(festivals_df)} festivals to {output_path}")
    return festivals_df


def collect_sample_lineup_data():
    """Create sample lineup data for testing."""
    print("Creating sample lineup data...")
    
    warehouse_dir = project_root / "warehouse" / "raw"
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    
    # Sample lineup data for Lollapalooza 2024
    sample_lineup = [
        {
            "festival_id": "lollapalooza",
            "year": 2024,
            "artist_id": "f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
            "artist_name": "The Weeknd",
            "billing_tier": "headliner",
            "day_of_festival": 1,
            "stage": "Main Stage",
            "source": "musicbrainz",
        },
        {
            "festival_id": "lollapalooza",
            "year": 2024,
            "artist_id": "1d79c3f2-6e01-4f73-994b-8a69b8c2b9e0",
            "artist_name": "Taylor Swift",
            "billing_tier": "headliner",
            "day_of_festival": 2,
            "stage": "Main Stage",
            "source": "musicbrainz",
        },
        {
            "festival_id": "lollapalooza",
            "year": 2024,
            "artist_id": "4d5447d7-2a38-4b59-8a30-5c721f39add8",
            "artist_name": "Drake",
            "billing_tier": "sub_headliner",
            "day_of_festival": 1,
            "stage": "Main Stage",
            "source": "musicbrainz",
        },
    ]
    
    lineup_df = pl.DataFrame(sample_lineup)
    output_path = warehouse_dir / "sample_lineups.parquet"
    lineup_df.write_parquet(output_path)
    
    print(f"Saved sample lineup data to {output_path}")
    return lineup_df


def collect_sample_artist_data():
    """Create sample artist data for testing."""
    print("Creating sample artist data...")
    
    warehouse_dir = project_root / "warehouse" / "normalized"
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    
    # Sample artist data
    sample_artists = [
        {
            "musicbrainz_id": "f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
            "normalized_name": "the weeknd",
            "name": "The Weeknd",
            "country": "CA",
            "genre": "R&B",
            "genres": ["R&B", "Pop", "Electropop"],
            "formed_year": 2010,
        },
        {
            "musicbrainz_id": "1d79c3f2-6e01-4f73-994b-8a69b8c2b9e0",
            "normalized_name": "taylor swift",
            "name": "Taylor Swift",
            "country": "US",
            "genre": "Pop",
            "genres": ["Pop", "Country", "Rock"],
            "formed_year": 2006,
        },
        {
            "musicbrainz_id": "4d5447d7-2a38-4b59-8a30-5c721f39add8",
            "normalized_name": "drake",
            "name": "Drake",
            "country": "CA",
            "genre": "Hip-Hop",
            "genres": ["Hip-Hop", "R&B", "Pop"],
            "formed_year": 2006,
        },
    ]
    
    artists_df = pl.DataFrame(sample_artists)
    output_path = warehouse_dir / "artists.parquet"
    artists_df.write_parquet(output_path)
    
    print(f"Saved sample artist data to {output_path}")
    return artists_df


def main():
    parser = argparse.ArgumentParser(description="Collect data for Festival Intelligence Terminal")
    parser.add_argument("--festivals", action="store_true", help="Collect festival data")
    parser.add_argument("--lineups", action="store_true", help="Create sample lineup data")
    parser.add_argument("--artists", action="store_true", help="Create sample artist data")
    parser.add_argument("--all", action="store_true", help="Collect all data")
    
    args = parser.parse_args()
    
    if args.all or args.festivals:
        collect_festival_data()
    
    if args.all or args.lineups:
        collect_sample_lineup_data()
    
    if args.all or args.artists:
        collect_sample_artist_data()
    
    if not any([args.festivals, args.lineups, args.artists, args.all]):
        print("No data collection specified. Use --help for options.")
        parser.print_help()


if __name__ == "__main__":
    main()
