"""
Test script for C3 portfolio parsers
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from c3 import ParserFactory, parse_lineup

def test_parser_factory():
    """Test parser factory"""
    print("Testing C3 Parser Factory")
    print("=" * 60)
    
    try:
        # Test getting parsers
        print(f"\nAvailable parsers:")
        for format_type in ["poster_grid", "day_stage_schedule", "multi_weekend", "simple_list"]:
            parser = ParserFactory.create_parser(format_type, festival_id="lollapalooza", year=2024)
            print(f"  {format_type}: {parser.__class__.__name__}")
        
        print(f"\n✓ Parser factory working")
        return True
        
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_simple_list_parser():
    """Test simple list parser"""
    print("\n\nTesting Simple List Parser")
    print("=" * 60)
    
    try:
        # Test data
        lineup_text = """
        Radiohead
        Kendrick Lamar
        Billie Eilish
        The Weeknd
        """
        
        parser = ParserFactory.create_parser("simple_list", festival_id="lollapalooza", year=2024)
        result = parser.parse(lineup_text)
        
        if result and result.artists:
            print(f"✓ Simple list parsing successful!")
            print(f"  Parsed {len(result.artists)} artists")
            for i, artist in enumerate(result.artists[:5]):
                print(f"    {i+1}. {artist.name}")
            return True
        else:
            print(f"✗ Simple list parsing failed: no artists parsed")
            return False
            
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_parse_lineup_function():
    """Test the main parse_lineup function"""
    print("\n\nTesting Main parse_lineup Function")
    print("=" * 60)
    
    try:
        # Test data
        lineup_text = """
        Radiohead
        Kendrick Lamar
        Billie Eilish
        """
        
        result = parse_lineup(lineup_text, format_profile="simple_list", festival_id="lollapalooza", year=2024)
        
        if result and result.artists:
            print(f"✓ Main parse_lineup function working!")
            print(f"  Parsed {len(result.artists)} artists")
            return True
        else:
            print(f"✗ parse_lineup failed: no artists parsed")
            return False
            
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Festival Bloomberg - C3 Portfolio Parser Test")
    print("=" * 60)
    
    # Run tests
    success = True
    success &= test_parser_factory()
    success &= test_simple_list_parser()
    success &= test_parse_lineup_function()
    
    print("\n" + "=" * 60)
    if success:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed")
        sys.exit(1)
