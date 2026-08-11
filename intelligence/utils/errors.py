"""
Error handling utilities for Festival Intelligence Terminal.
Custom exceptions and error response formatting.
"""
from typing import Dict, Any, Optional
from enum import Enum
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse


class ErrorCode(Enum):
    """Standard error codes."""
    # General errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    
    # Database errors
    DATABASE_ERROR = "DATABASE_ERROR"
    DATABASE_CONNECTION_ERROR = "DATABASE_CONNECTION_ERROR"
    
    # API errors
    MONID_ERROR = "MONID_ERROR"
    EXTERNAL_API_ERROR = "EXTERNAL_API_ERROR"
    
    # Validation errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATA_QUALITY_ERROR = "DATA_QUALITY_ERROR"
    
    # Business logic errors
    ARTIST_NOT_FOUND = "ARTIST_NOT_FOUND"
    FESTIVAL_NOT_FOUND = "FESTIVAL_NOT_FOUND"
    INVALID_PREDICTION = "INVALID_PREDICTION"


class FestivalIntelligenceError(Exception):
    """Base exception for Festival Intelligence Terminal."""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


class DatabaseError(FestivalIntelligenceError):
    """Database-related errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, ErrorCode.DATABASE_ERROR, details)


class MonidError(FestivalIntelligenceError):
    """Monid.ai-related errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, ErrorCode.MONID_ERROR, details)


class ValidationError(FestivalIntelligenceError):
    """Data validation errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, ErrorCode.VALIDATION_ERROR, details)


class ArtistNotFoundError(FestivalIntelligenceError):
    """Artist not found error."""
    
    def __init__(self, artist_id: str, details: Optional[Dict[str, Any]] = None):
        message = f"Artist not found: {artist_id}"
        details = details or {"artist_id": artist_id}
        super().__init__(message, ErrorCode.ARTIST_NOT_FOUND, details)


class FestivalNotFoundError(FestivalIntelligenceError):
    """Festival not found error."""
    
    def __init__(self, festival_id: str, details: Optional[Dict[str, Any]] = None):
        message = f"Festival not found: {festival_id}"
        details = details or {"festival_id": festival_id}
        super().__init__(message, ErrorCode.FESTIVAL_NOT_FOUND, details)


def error_response(
    error: FestivalIntelligenceError,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
) -> JSONResponse:
    """
    Format error as JSON response.
    
    Args:
        error: The FestivalIntelligenceError
        status_code: HTTP status code
        
    Returns:
        JSONResponse with error details
    """
    content = {
        "error": {
            "code": error.error_code.value,
            "message": error.message,
            "details": error.details
        }
    }
    return JSONResponse(status_code=status_code, content=content)


def handle_exception(exception: Exception) -> JSONResponse:
    """
    Handle generic exceptions and convert to FestivalIntelligenceError.
    
    Args:
        exception: The caught exception
        
    Returns:
        JSONResponse with error details
    """
    if isinstance(exception, FestivalIntelligenceError):
        # Map error codes to HTTP status codes
        status_map = {
            ErrorCode.ARTIST_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.FESTIVAL_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.INVALID_REQUEST: status.HTTP_400_BAD_REQUEST,
            ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
            ErrorCode.UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
            ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
            ErrorCode.DATABASE_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorCode.MONID_ERROR: status.HTTP_502_BAD_GATEWAY,
            ErrorCode.EXTERNAL_API_ERROR: status.HTTP_502_BAD_GATEWAY,
        }
        
        status_code = status_map.get(exception.error_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        return error_response(exception, status_code)
    
    # Handle generic exceptions
    return error_response(
        FestivalIntelligenceError(
            message=str(exception),
            error_code=ErrorCode.INTERNAL_ERROR,
            details={"exception_type": type(exception).__name__}
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def create_http_exception(
    error: FestivalIntelligenceError,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
) -> HTTPException:
    """
    Convert FestivalIntelligenceError to HTTPException.
    
    Args:
        error: The FestivalIntelligenceError
        status_code: HTTP status code
        
    Returns:
        HTTPException
    """
    return HTTPException(
        status_code=status_code,
        detail={
            "code": error.error_code.value,
            "message": error.message,
            "details": error.details
        }
    )
