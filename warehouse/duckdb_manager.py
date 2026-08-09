"""
DuckDB Warehouse Manager
Implements analytical warehouse per Festival Bloomberg spec
"""
import os
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


class DuckDBWarehouse:
    """
    DuckDB warehouse for Festival Bloomberg analytical processing
    Implements local aggregation and feature generation
    """
    
    def __init__(self, db_path: str, read_only: bool = False):
        """
        Initialize DuckDB warehouse
        
        Args:
            db_path: Path to DuckDB database file
            read_only: Whether to open in read-only mode
        """
        self.db_path = db_path
        self.read_only = read_only
        self._connection = None
        self._initialize_connection()
        self._create_schemas()
    
    def _initialize_connection(self):
        """Initialize DuckDB connection"""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            self._connection = duckdb.connect(
                self.db_path,
                read_only=self.read_only
            )
            
            logger.info(f"DuckDB warehouse initialized: {self.db_path}")
            
        except Exception as e:
            logger.error(f"Failed to initialize DuckDB connection: {e}")
            raise
    
    @property
    def connection(self):
        """Get DuckDB connection"""
        if self._connection is None:
            self._initialize_connection()
        return self._connection
    
    def _create_schemas(self):
        """Create standard schemas per Festival Bloomberg spec.

        No-op when the connection is read-only (schemas already exist from a
        prior writable session); attempting CREATE on a read-only DB raises.
        """
        if self.read_only:
            logger.debug("Read-only connection; skipping schema creation")
            return
        schemas = ['raw', 'core', 'metrics', 'model', 'audit']

        for schema in schemas:
            try:
                self.connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                logger.debug(f"Created schema: {schema}")
            except Exception as e:
                logger.error(f"Failed to create schema {schema}: {e}")
    
    def execute_sql(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> duckdb.DuckDBPyResult:
        """
        Execute SQL query
        
        Args:
            query: SQL query string
            parameters: Optional parameters for parameterized queries
            
        Returns:
            DuckDB result object
        """
        try:
            if parameters:
                result = self.connection.execute(query, parameters)
            else:
                result = self.connection.execute(query)
            
            logger.debug(f"Executed SQL query: {query[:100]}...")
            return result
            
        except Exception as e:
            logger.error(f"SQL execution failed: {e}")
            logger.error(f"Query: {query}")
            raise
    
    def execute_to_df(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """
        Execute SQL query and return as DataFrame
        
        Args:
            query: SQL query string
            parameters: Optional parameters
            
        Returns:
            Pandas DataFrame
        """
        try:
            result = self.execute_sql(query, parameters)
            df = result.df()
            logger.debug(f"Query returned {len(df)} rows")
            return df
            
        except Exception as e:
            logger.error(f"Failed to execute query to DataFrame: {e}")
            raise
    
    def execute_to_arrow(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> pa.Table:
        """
        Execute SQL query and return as Arrow table
        
        Args:
            query: SQL query string
            parameters: Optional parameters
            
        Returns:
            PyArrow Table
        """
        try:
            result = self.execute_sql(query, parameters)
            table = result.arrow()
            logger.debug(f"Query returned {len(table)} rows as Arrow table")
            return table
            
        except Exception as e:
            logger.error(f"Failed to execute query to Arrow: {e}")
            raise
    
    def register_dataframe(self, df: pd.DataFrame, table_name: str, schema: str = 'core'):
        """
        Register Pandas DataFrame as a DuckDB table/view
        
        Args:
            df: Pandas DataFrame
            table_name: Name for the registered table
            schema: Schema to register in
        """
        try:
            full_name = f"{schema}.{table_name}"
            self.connection.register(table_name, df)
            logger.info(f"Registered DataFrame as {full_name}")
            
        except Exception as e:
            logger.error(f"Failed to register DataFrame: {e}")
            raise
    
    def create_table_from_df(self, 
                           df: pd.DataFrame, 
                           table_name: str, 
                           schema: str = 'core',
                           overwrite: bool = False):
        """
        Create persistent table from DataFrame
        
        Args:
            df: Pandas DataFrame
            table_name: Table name
            schema: Schema name
            overwrite: Whether to overwrite existing table
        """
        try:
            full_name = f"{schema}.{table_name}"
            
            if overwrite:
                self.connection.execute(f"DROP TABLE IF EXISTS {full_name}")
            
            self.connection.register(table_name, df)
            self.connection.execute(f"CREATE TABLE {full_name} AS SELECT * FROM {table_name}")
            
            logger.info(f"Created table {full_name} from DataFrame ({len(df)} rows)")
            
        except Exception as e:
            logger.error(f"Failed to create table from DataFrame: {e}")
            raise
    
    def import_parquet(self, 
                      file_path: str, 
                      table_name: str, 
                      schema: str = 'raw',
                      overwrite: bool = False):
        """
        Import Parquet file into DuckDB
        
        Args:
            file_path: Path to Parquet file
            table_name: Table name
            schema: Schema name
            overwrite: Whether to overwrite existing table
        """
        try:
            full_name = f"{schema}.{table_name}"
            
            if overwrite:
                self.connection.execute(f"DROP TABLE IF EXISTS {full_name}")
            
            self.connection.execute(f"""
                CREATE TABLE {full_name} AS 
                SELECT * FROM read_parquet('{file_path}')
            """)
            
            logger.info(f"Imported Parquet to {full_name}")
            
        except Exception as e:
            logger.error(f"Failed to import Parquet: {e}")
            raise
    
    def export_to_parquet(self,
                         query: str,
                         output_path: str,
                         compression: str = 'snappy',
                         partition_by: Optional[List[str]] = None):
        """
        Export query results to Parquet
        
        Args:
            query: SQL query to export
            output_path: Output file path
            compression: Compression codec (snappy, gzip, etc.)
            partition_by: Optional partitioning columns
        """
        try:
            if partition_by:
                self.connection.execute(f"""
                    COPY ({query}) TO '{output_path}' 
                    (FORMAT PARQUET, COMPRESSION {compression}, PARTITION_BY ({', '.join(partition_by)}))
                """)
            else:
                self.connection.execute(f"""
                    COPY ({query}) TO '{output_path}' 
                    (FORMAT PARQUET, COMPRESSION {compression})
                """)
            
            logger.info(f"Exported query results to {output_path}")
            
        except Exception as e:
            logger.error(f"Failed to export to Parquet: {e}")
            raise
    
    def export_to_csv(self,
                    query: str,
                    output_path: str,
                    header: bool = True):
        """
        Export query results to CSV
        
        Args:
            query: SQL query to export
            output_path: Output file path
            header: Whether to include header row
        """
        try:
            header_str = "TRUE" if header else "FALSE"
            self.connection.execute(f"""
                COPY ({query}) TO '{output_path}' 
                (FORMAT CSV, HEADER {header_str})
            """)
            
            logger.info(f"Exported query results to {output_path}")
            
        except Exception as e:
            logger.error(f"Failed to export to CSV: {e}")
            raise
    
    def create_view(self, view_name: str, query: str, schema: str = 'core', replace: bool = True):
        """
        Create a view from a query
        
        Args:
            view_name: View name
            query: SQL query for the view
            schema: Schema name
            replace: Whether to replace existing view
        """
        try:
            full_name = f"{schema}.{view_name}"
            replace_str = "OR REPLACE" if replace else ""
            
            self.connection.execute(f"""
                CREATE {replace_str} VIEW {full_name} AS {query}
            """)
            
            logger.info(f"Created view {full_name}")
            
        except Exception as e:
            logger.error(f"Failed to create view: {e}")
            raise
    
    def get_table_info(self, table_name: str, schema: str = 'core') -> pd.DataFrame:
        """
        Get table schema information
        
        Args:
            table_name: Table name
            schema: Schema name
            
        Returns:
            DataFrame with column information
        """
        try:
            full_name = f"{schema}.{table_name}"
            result = self.connection.execute(f"DESCRIBE {full_name}")
            return result.df()
            
        except Exception as e:
            logger.error(f"Failed to get table info: {e}")
            raise
    
    def get_table_count(self, table_name: str, schema: str = 'core') -> int:
        """
        Get row count for a table
        
        Args:
            table_name: Table name
            schema: Schema name
            
        Returns:
            Row count
        """
        try:
            full_name = f"{schema}.{table_name}"
            result = self.connection.execute(f"SELECT COUNT(*) FROM {full_name}")
            return result.fetchone()[0]
            
        except Exception as e:
            logger.error(f"Failed to get table count: {e}")
            raise
    
    def create_analytical_mart(self, mart_name: str, query: str, schema: str = 'core'):
        """
        Create an analytical mart (materialized view)
        
        Args:
            mart_name: Mart name
            query: SQL query for the mart
            schema: Schema name
        """
        try:
            full_name = f"{schema}.{mart_name}"
            
            # Drop existing mart
            self.connection.execute(f"DROP TABLE IF EXISTS {full_name}")
            
            # Create new mart
            self.connection.execute(f"""
                CREATE TABLE {full_name} AS {query}
            """)
            
            logger.info(f"Created analytical mart {full_name}")
            
        except Exception as e:
            logger.error(f"Failed to create analytical mart: {e}")
            raise
    
    def refresh_analytical_mart(self, mart_name: str, query: str, schema: str = 'core'):
        """
        Refresh an analytical mart
        
        Args:
            mart_name: Mart name
            query: SQL query for the mart
            schema: Schema name
        """
        try:
            full_name = f"{schema}.{mart_name}"
            
            # Create temp table
            temp_name = f"{mart_name}_temp"
            temp_full = f"{schema}.{temp_name}"
            
            self.connection.execute(f"DROP TABLE IF EXISTS {temp_full}")
            self.connection.execute(f"CREATE TABLE {temp_full} AS {query}")
            
            # Swap tables
            self.connection.execute(f"DROP TABLE IF EXISTS {full_name}")
            self.connection.execute(f"ALTER TABLE {temp_full} RENAME TO {mart_name}")
            
            logger.info(f"Refreshed analytical mart {full_name}")
            
        except Exception as e:
            logger.error(f"Failed to refresh analytical mart: {e}")
            raise
    
    def log_run(self, 
                run_id: str,
                source_system: str,
                started_at: datetime,
                finished_at: Optional[datetime] = None,
                status: str = 'running',
                records_read: int = 0,
                records_written: int = 0,
                error_count: int = 0,
                parser_version: Optional[str] = None,
                parameters: Optional[Dict[str, Any]] = None):
        """
        Log a pipeline run to audit schema
        
        Args:
            run_id: Unique run identifier
            source_system: Source system name
            started_at: Start timestamp
            finished_at: End timestamp (optional)
            status: Run status
            records_read: Number of records read
            records_written: Number of records written
            error_count: Number of errors
            parser_version: Parser version
            parameters: Run parameters
        """
        try:
            self.connection.execute("""
                INSERT INTO audit.ingest_run 
                (run_id, source_system, started_at, finished_at, status, 
                 records_read, records_written, error_count, parser_version, parameters)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                run_id,
                source_system,
                started_at,
                finished_at,
                status,
                records_read,
                records_written,
                error_count,
                parser_version,
                str(parameters) if parameters else None
            ])
            
            logger.info(f"Logged run {run_id} to audit.ingest_run")
            
        except Exception as e:
            logger.error(f"Failed to log run: {e}")
            raise
    
    def log_error(self,
                 run_id: str,
                 source_url: Optional[str],
                 record_key: Optional[str],
                 error_type: str,
                 error_message: str,
                 payload: Optional[Dict[str, Any]] = None):
        """
        Log an error to audit schema
        
        Args:
            run_id: Run identifier
            source_url: Source URL
            record_key: Record key
            error_type: Error type
            error_message: Error message
            payload: Error payload
        """
        try:
            self.connection.execute("""
                INSERT INTO audit.ingest_error 
                (run_id, source_url, record_key, error_type, error_message, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                run_id,
                source_url,
                record_key,
                error_type,
                error_message,
                str(payload) if payload else None,
                datetime.utcnow()
            ])
            
            logger.info(f"Logged error for run {run_id}")
            
        except Exception as e:
            logger.error(f"Failed to log error: {e}")
            raise
    
    def close(self):
        """Close DuckDB connection"""
        if self._connection:
            self._connection.close()
            self._connection = None
            logger.info("DuckDB connection closed")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


def create_warehouse(db_path: str, read_only: bool = False) -> DuckDBWarehouse:
    """
    Factory function to create DuckDB warehouse
    
    Args:
        db_path: Path to DuckDB database file
        read_only: Whether to open in read-only mode
        
    Returns:
        DuckDBWarehouse instance
    """
    return DuckDBWarehouse(db_path, read_only)
