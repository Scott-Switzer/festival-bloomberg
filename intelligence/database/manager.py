"""
Database manager for Festival Intelligence Terminal.
Handles database connections, sessions, and migrations.
"""
from contextlib import contextmanager
from typing import Optional, Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

from config import get_config
from . import Base, create_engine_from_config


class DatabaseManager:
    """Manages database connections and sessions."""
    
    def __init__(self):
        self.config = get_config()
        self.engine = None
        self.SessionLocal = None
        self._initialize_engine()
    
    def _initialize_engine(self):
        """Initialize database engine from configuration."""
        if not self.config.database_config:
            raise ValueError("Database configuration not found")
        
        try:
            self.engine = create_engine_from_config(self.config.database_config)
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
            print(f"✓ Database engine initialized: {self.config.database_config.host}:{self.config.database_config.port}/{self.config.database_config.database}")
        except Exception as e:
            print(f"✗ Failed to initialize database engine: {e}")
            raise
    
    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Get a database session with automatic cleanup."""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Database error: {e}")
            raise
        finally:
            session.close()
    
    def create_tables(self):
        """Create all database tables."""
        try:
            Base.metadata.create_all(self.engine)
            print("✓ Database tables created successfully")
        except SQLAlchemyError as e:
            print(f"✗ Failed to create tables: {e}")
            raise
    
    def drop_tables(self):
        """Drop all database tables."""
        try:
            Base.metadata.drop_all(self.engine)
            print("✓ Database tables dropped successfully")
        except SQLAlchemyError as e:
            print(f"✗ Failed to drop tables: {e}")
            raise
    
    def test_connection(self) -> bool:
        """Test database connection."""
        try:
            with self.engine.connect() as connection:
                connection.execute("SELECT 1")
            print("✓ Database connection successful")
            return True
        except Exception as e:
            print(f"✗ Database connection failed: {e}")
            return False
    
    def get_table_info(self) -> dict:
        """Get information about database tables."""
        try:
            inspector = self.engine.dialect.get_inspector(self.engine)
            tables = inspector.get_table_names()
            
            table_info = {}
            for table in tables:
                columns = inspector.get_columns(table)
                table_info[table] = {
                    'columns': [col['name'] for col in columns],
                    'column_count': len(columns)
                }
            
            return table_info
        except Exception as e:
            print(f"Failed to get table info: {e}")
            return {}


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """Get global database manager instance."""
    global _db_manager
    
    if _db_manager is None:
        _db_manager = DatabaseManager()
    
    return _db_manager


def reset_db_manager():
    """Reset global database manager (useful for testing)."""
    global _db_manager
    _db_manager = None
