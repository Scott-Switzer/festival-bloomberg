"""
Database initialization script.
Creates database tables and sets up initial data.
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.manager import get_db_manager
from database import Artist, Festival, User


def init_database():
    """Initialize database with tables and seed data."""
    print("=" * 60)
    print("Festival IntelligenceTerminal - Database Initialization")
    print("=" * 60)
    
    try:
        # Get database manager
        db_manager = get_db_manager()
        
        # Test connection
        print("\nTesting database connection...")
        if not db_manager.test_connection():
            print("✗ Database connection failed. Please check your configuration.")
            return False
        
        # Create tables
        print("\nCreating database tables...")
        db_manager.create_tables()
        
        # Show table info
        print("\nDatabase tables created:")
        table_info = db_manager.get_table_info()
        for table_name, info in table_info.items():
            print(f"  - {table_name}: {info['column_count']} columns")
        
        # Seed initial data
        print("\nSeeding initial data...")
        seed_database(db_manager)
        
        print("\n" + "=" * 60)
        print("Database initialization complete!")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n✗ Database initialization failed: {e}")
        return False


def seed_database(db_manager):
    """Seed database with initial data."""
    try:
        with db_manager.get_session() as session:
            # Check if data already exists
            existing_artists = session.query(Artist).count()
            if existing_artists > 0:
                print("  - Database already contains data, skipping seed")
                return
            
            # Create sample artist
            sample_artist = Artist(
                name="Sample Artist",
                normalized_name="sample artist",
                genres=["pop", "electronic"],
                origin_country="US",
                career_stage="emerging",
                monthly_listeners=100000,
                momentum_score=0.7
            )
            session.add(sample_artist)
            
            # Create sample festival
            sample_festival = Festival(
                name="Sample Festival",
                normalized_name="sample festival",
                location_country="US",
                location_city="New York",
                capacity=50000,
                genre_focus=["pop", "electronic"],
                festival_type="music",
                venue_type="outdoor",
                duration_days=3
            )
            session.add(sample_festival)
            
            # Create admin user
            admin_user = User(
                email="admin@festival-intelligence.com",
                name="Admin User",
                role="admin",
                verification_status="verified"
            )
            session.add(admin_user)
            
            session.commit()
            print("  - Sample data seeded successfully")
            
    except Exception as e:
        print(f"  - Warning: Failed to seed data: {e}")


if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)
