"""
Test script for Monid.ai integration.
"""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

# Add project root to path
sys.path.insert(0, str(project_root))

from pipelines.monid import MonidClient, FestivalIntelligenceAgent


def test_monid_connection():
    """Test Monid.ai connection and authentication."""
    print("=" * 60)
    print("Testing Monid.ai Integration")
    print("=" * 60)
    
    try:
        # Initialize client
        print("\n1. Initializing Monid client...")
        client = MonidClient()
        print("✓ Client initialized successfully")
        
        # Check balance
        print("\n2. Checking wallet balance...")
        balance = client.get_balance()
        
        # Discover tools
        print("\n3. Discovering tools for Spotify artist data...")
        tools = client.discover("Spotify artist data")
        
        if tools:
            print(f"\n✓ Found {len(tools)} tools:")
            for i, tool in enumerate(tools[:3], 1):  # Show first 3
                print(f"\n{i}. {tool.name}")
                print(f"   Description: {tool.description}")
                print(f"   Pricing: {tool.pricing}")
        
        # Test agent
        print("\n4. Testing Festival Intelligence Agent...")
        agent = FestivalIntelligenceAgent()
        
        # Discover data sources
        print("\n5. Discovering festival data sources...")
        festival_tools = agent.discover_data_sources("festival lineup data")
        
        print("\n" + "=" * 60)
        print("✓ Monid.ai integration test completed successfully")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_monid_connection()
    sys.exit(0 if success else 1)
