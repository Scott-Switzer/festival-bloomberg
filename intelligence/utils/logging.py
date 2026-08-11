"""
Logging configuration for Festival Intelligence Terminal.
Structured logging with file rotation and different log levels.
"""
import logging
import logging.handlers
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import get_config


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output."""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, '')
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None):
    """
    Set up logging configuration for the application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for log output
    """
    # Create logs directory if needed
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_formatter = ColoredFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation if log file specified
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)  # Log everything to file
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name."""
    return logging.getLogger(name)


class APILogger:
    """Logger for API requests and responses."""
    
    def __init__(self):
        self.logger = get_logger("api")
    
    def log_request(self, method: str, path: str, client_ip: str = None):
        """Log incoming API request."""
        self.logger.info(f"{method} {path} - Client: {client_ip or 'unknown'}")
    
    def log_response(self, method: str, path: str, status_code: int, duration_ms: float):
        """Log API response."""
        self.logger.info(f"{method} {path} - Status: {status_code} - Duration: {duration_ms:.2f}ms")
    
    def log_error(self, method: str, path: str, error: Exception):
        """Log API error."""
        self.logger.error(f"{method} {path} - Error: {str(error)}", exc_info=True)


class DatabaseLogger:
    """Logger for database operations."""
    
    def __init__(self):
        self.logger = get_logger("database")
    
    def log_query(self, query_type: str, table: str, duration_ms: float):
        """Log database query."""
        self.logger.debug(f"{query_type} on {table} - Duration: {duration_ms:.2f}ms")
    
    def log_error(self, operation: str, error: Exception):
        """Log database error."""
        self.logger.error(f"Database {operation} - Error: {str(error)}", exc_info=True)


class MonidLogger:
    """Logger for Monid.ai operations."""
    
    def __init__(self):
        self.logger = get_logger("monid")
    
    def log_tool_discovery(self, query: str, tools_found: int):
        """Log tool discovery."""
        self.logger.info(f"Tool discovery: '{query}' - Found {tools_found} tools")
    
    def log_tool_execution(self, tool_id: str, duration_ms: float):
        """Log tool execution."""
        self.logger.info(f"Tool execution: {tool_id} - Duration: {duration_ms:.2f}ms")
    
    def log_error(self, operation: str, error: Exception):
        """Log Monid.ai error."""
        self.logger.error(f"Monid.ai {operation} - Error: {str(error)}", exc_info=True)


# Initialize logging on module import
def initialize_logging():
    """Initialize logging from configuration."""
    try:
        config = get_config()
        logging_config = config.logging_config
        
        if logging_config:
            setup_logging(
                log_level=logging_config.level,
                log_file=logging_config.file_path
            )
        else:
            # Default configuration
            setup_logging(log_level="INFO", log_file="logs/festival_intelligence.log")
        
        get_logger(__name__).info("Logging initialized successfully")
    except Exception as e:
        # Fallback to basic logging if configuration fails
        logging.basicConfig(level=logging.INFO)
        logging.error(f"Failed to initialize logging from config: {e}")


# Auto-initialize on import
initialize_logging()
