"""
Storage layer for Festival Bloomberg
Implements Cloudflare R2 object storage integration
"""
from .r2_client import R2Client, R2Config, create_r2_client_from_env

__all__ = ['R2Client', 'R2Config', 'create_r2_client_from_env']
