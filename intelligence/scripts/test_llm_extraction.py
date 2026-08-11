"""
Test script for LLM extraction with NVIDIA API
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

from extraction import LLMExtractor, ExtractionModel

def test_artist_extraction():
    """Test artist extraction with NVIDIA API"""
    print("Testing LLM Artist Extraction with NVIDIA API")
    print("=" * 60)
    
    # Test content
    test_content = """
    Radiohead is an English rock band formed in Abingdon, Oxfordshire in 1985. 
    The band consists of Thom Yorke (lead vocals, guitar, piano), Jonny Greenwood (lead guitar, keyboards), 
    Colin Greenwood (bass), Ed O'Brien (guitar, backing vocals), and Philip Selway (drums, percussion).
    They have released albums including OK Computer, Kid A, and In Rainbows. Their music spans genres 
    including alternative rock, art rock, and electronic music.
    """
    
    try:
        # Initialize extractor with Llama 3.1 8B (worked in simple test)
        extractor = LLMExtractor(model=ExtractionModel.NVIDIA_LLAMA_3_1_8B)
        print(f"✓ LLM Extractor initialized")
        print(f"  Model: {extractor.model.value}")
        print(f"  Base URL: {extractor._client.base_url}")
        
        # Test extraction
        print(f"\nExtracting artist information...")
        result = extractor.extract_artist(test_content)
        
        if result.success:
            print(f"✓ Extraction successful!")
            print(f"\nExtracted Data:")
            print(f"  Name: {result.data.name}")
            print(f"  Country: {result.data.country}")
            print(f"  Formed Year: {result.data.formed_year}")
            print(f"  Genres: {', '.join(result.data.genres)}")
            print(f"  Confidence: {result.data.confidence}")
            print(f"  Description: {result.data.description[:100]}..." if result.data.description else "")
            
            print(f"\nExtraction Metrics:")
            print(f"  Tokens Used: {result.tokens_used}")
            print(f"  Cost Estimate: ${result.cost_estimate:.4f}")
            print(f"  Extraction Time: {result.extraction_time:.2f}s")
            
            return True
        else:
            print(f"✗ Extraction failed: {result.error}")
            return False
            
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_festival_extraction():
    """Test festival extraction"""
    print("\n\nTesting LLM Festival Extraction")
    print("=" * 60)
    
    test_content = """
    Lollapalooza is an annual music festival held in Grant Park, Chicago, Illinois. 
    Founded in 1991, it features alternative rock, heavy metal, punk rock, and hip-hop bands. 
    The festival typically spans 4 days with multiple stages.
    """
    
    try:
        # Use same model that worked for artist extraction
        extractor = LLMExtractor(model=ExtractionModel.NVIDIA_LLAMA_3_1_8B)
        result = extractor.extract_festival(test_content)
        
        if result.success:
            print(f"✓ Festival extraction successful!")
            print(f"  Name: {result.data.name}")
            print(f"  Genres: {', '.join(result.data.genres)}")
            print(f"  Description: {result.data.description[:100] if result.data.description else 'None'}...")
            return True
        else:
            print(f"✗ Festival extraction failed: {result.error}")
            return False
            
    except Exception as e:
        print(f"✗ Festival test failed: {e}")
        return False

if __name__ == "__main__":
    print("Festival Bloomberg - LLM Extraction Test")
    print("=" * 60)
    
    # Check for NVIDIA API key
    if not os.getenv('NVIDIA_API_KEY'):
        print("✗ NVIDIA_API_KEY not found in environment")
        print("Please set NVIDIA_API_KEY in .env file")
        sys.exit(1)
    
    print(f"✓ NVIDIA_API_KEY found")
    
    # Run tests
    success = True
    success &= test_artist_extraction()
    success &= test_festival_extraction()
    
    print("\n" + "=" * 60)
    if success:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed")
        sys.exit(1)
