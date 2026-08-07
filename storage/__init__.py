"""
Storage layer for Festival Bloomberg
Implements Cloudflare R2 object storage with local fallback
"""
from .r2_client import R2Client, R2Config, create_r2_client_from_env
import os

__all__ = ['R2Client', 'R2Config', 'create_r2_client_from_env']

def create_r2_client_from_env(local_storage_dir: str = "data/local_storage") -> R2Client:
    """
    Create R2 client from environment variables
    Falls back to local storage if R2 not configured
    """
    config = R2Config(
        account_id=os.getenv('R2_ACCOUNT_ID'),
        access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
        secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
        bucket_name=os.getenv('R2_BUCKET_NAME')
    )
    return R2Client(config=config, local_storage_dir=local_storage_dir)
