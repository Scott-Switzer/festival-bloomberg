"""
Cloudflare R2 Object Storage Client
Implements R2 integration for immutable object storage per Festival Bloomberg spec
"""
import os
import hashlib
import logging
from typing import Optional, BinaryIO, Dict, Any
from datetime import datetime
import boto3
from botocore.exceptions import ClientError, BotoCoreError
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class R2Config:
    """R2 configuration"""
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    region: str = "auto"


class R2Client:
    """
    Cloudflare R2 client for Festival Bloomberg object storage
    Implements tiered storage patterns and content hashing
    """
    
    def __init__(self, config: R2Config):
        self.config = config
        self.endpoint_url = f"https://{config.account_id}.r2.cloudflarestorage.com"
        self._client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize boto3 S3 client for R2"""
        try:
            self._client = boto3.client(
                service_name='s3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.config.access_key_id,
                aws_secret_access_key=self.config.secret_access_key,
                region_name=self.config.region
            )
            logger.info(f"R2 client initialized for bucket: {self.config.bucket_name}")
        except Exception as e:
            logger.error(f"Failed to initialize R2 client: {e}")
            raise
    
    @property
    def client(self):
        """Lazy client initialization"""
        if self._client is None:
            self._initialize_client()
        return self._client
    
    def _generate_content_hash(self, data: bytes) -> str:
        """Generate SHA256 hash of content"""
        return hashlib.sha256(data).hexdigest()
    
    def _build_object_key(self, 
                         source_system: str, 
                         year: int, 
                         month: int, 
                         day: int, 
                         content_hash: str,
                         extension: str = "json") -> str:
        """
        Build R2 object key following Festival Bloomberg spec
        Format: raw/{source_system}/{YYYY}/{MM}/{DD}/{content_hash}.{ext}
        """
        return f"raw/{source_system}/{year:04d}/{month:02d}/{day:02d}/{content_hash}.{extension}"
    
    def _build_normalized_key(self,
                            schema_version: str,
                            entity_type: str,
                            year: int,
                            month: int,
                            day: int,
                            record_id: str) -> str:
        """
        Build normalized record key
        Format: normalized/{schema_version}/{entity_type}/{YYYY}/{MM}/{DD}/{record_id}.json
        """
        return f"normalized/{schema_version}/{entity_type}/{year:04d}/{month:02d}/{day:02d}/{record_id}.json"
    
    def _build_export_key(self,
                         run_id: str,
                         dataset_name: str) -> str:
        """
        Build export key
        Format: exports/{run_id}/{dataset_name}.parquet
        """
        return f"exports/{run_id}/{dataset_name}.parquet"
    
    def upload_raw_content(self,
                          source_system: str,
                          content: bytes,
                          extension: str = "json",
                          metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload raw content to R2 with content hashing
        Returns object key and metadata
        """
        try:
            content_hash = self._generate_content_hash(content)
            now = datetime.utcnow()
            
            object_key = self._build_object_key(
                source_system=source_system,
                year=now.year,
                month=now.month,
                day=now.day,
                content_hash=content_hash,
                extension=extension
            )
            
            # Prepare metadata
            upload_metadata = {
                'content_hash': content_hash,
                'content_length': str(len(content)),
                'source_system': source_system,
                'uploaded_at': now.isoformat(),
                'content_type': self._get_content_type(extension)
            }
            
            if metadata:
                upload_metadata.update(metadata)
            
            # Upload to R2
            self.client.put_object(
                Bucket=self.config.bucket_name,
                Key=object_key,
                Body=content,
                Metadata=upload_metadata,
                ContentType=upload_metadata['content_type']
            )
            
            logger.info(f"Uploaded raw content to R2: {object_key}")
            
            return {
                'object_key': object_key,
                'content_hash': content_hash,
                'content_length': len(content),
                'r2_uri': f"r2://{self.config.bucket_name}/{object_key}",
                'uploaded_at': now.isoformat(),
                'metadata': upload_metadata
            }
            
        except (ClientError, BotoCoreError) as e:
            logger.error(f"R2 upload failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during R2 upload: {e}")
            raise
    
    def upload_file(self,
                   file_path: str,
                   source_system: str,
                   extension: str = None,
                   metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload a file to R2
        """
        try:
            if extension is None:
                extension = file_path.split('.')[-1] if '.' in file_path else 'bin'
            
            with open(file_path, 'rb') as f:
                content = f.read()
            
            return self.upload_raw_content(source_system, content, extension, metadata)
            
        except Exception as e:
            logger.error(f"Failed to upload file {file_path}: {e}")
            raise
    
    def download_content(self, object_key: str) -> bytes:
        """
        Download content from R2 by object key
        """
        try:
            response = self.client.get_object(
                Bucket=self.config.bucket_name,
                Key=object_key
            )
            
            content = response['Body'].read()
            logger.info(f"Downloaded content from R2: {object_key}")
            
            return content
            
        except (ClientError, BotoCoreError) as e:
            logger.error(f"R2 download failed for {object_key}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during R2 download: {e}")
            raise
    
    def download_to_file(self, object_key: str, file_path: str) -> None:
        """
        Download content from R2 and save to file
        """
        try:
            content = self.download_content(object_key)
            
            with open(file_path, 'wb') as f:
                f.write(content)
            
            logger.info(f"Downloaded {object_key} to {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to download {object_key} to {file_path}: {e}")
            raise
    
    def check_exists(self, object_key: str) -> bool:
        """
        Check if object exists in R2
        """
        try:
            self.client.head_object(
                Bucket=self.config.bucket_name,
                Key=object_key
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"Error checking object existence: {e}")
            raise
    
    def get_object_metadata(self, object_key: str) -> Dict[str, Any]:
        """
        Get object metadata without downloading content
        """
        try:
            response = self.client.head_object(
                Bucket=self.config.bucket_name,
                Key=object_key
            )
            
            return {
                'content_length': response['ContentLength'],
                'content_type': response['ContentType'],
                'last_modified': response['LastModified'].isoformat(),
                'metadata': response.get('Metadata', {}),
                'etag': response['ETag']
            }
            
        except ClientError as e:
            logger.error(f"Failed to get metadata for {object_key}: {e}")
            raise
    
    def delete_object(self, object_key: str) -> bool:
        """
        Delete object from R2
        """
        try:
            self.client.delete_object(
                Bucket=self.config.bucket_name,
                Key=object_key
            )
            logger.info(f"Deleted object from R2: {object_key}")
            return True
            
        except ClientError as e:
            logger.error(f"Failed to delete {object_key}: {e}")
            return False
    
    def list_objects(self, 
                    prefix: str, 
                    max_keys: int = 1000) -> list:
        """
        List objects with given prefix
        """
        try:
            response = self.client.list_objects_v2(
                Bucket=self.config.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            objects = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    objects.append({
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat(),
                        'etag': obj['ETag']
                    })
            
            return objects
            
        except ClientError as e:
            logger.error(f"Failed to list objects with prefix {prefix}: {e}")
            raise
    
    def generate_presigned_url(self,
                             object_key: str,
                             operation: str = 'get_object',
                             expires_in: int = 3600) -> str:
        """
        Generate presigned URL for temporary access
        """
        try:
            if operation == 'get_object':
                url = self.client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': self.config.bucket_name, 'Key': object_key},
                    ExpiresIn=expires_in
                )
            elif operation == 'put_object':
                url = self.client.generate_presigned_url(
                    'put_object',
                    Params={
                        'Bucket': self.config.bucket_name,
                        'Key': object_key,
                        'ContentType': 'application/json'
                    },
                    ExpiresIn=expires_in
                )
            else:
                raise ValueError(f"Unsupported operation: {operation}")
            
            logger.info(f"Generated presigned URL for {object_key}")
            return url
            
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise
    
    def _get_content_type(self, extension: str) -> str:
        """Map file extension to content type"""
        content_types = {
            'json': 'application/json',
            'parquet': 'application/octet-stream',
            'csv': 'text/csv',
            'txt': 'text/plain',
            'html': 'text/html',
            'xml': 'application/xml',
            'pdf': 'application/pdf',
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp',
            'svg': 'image/svg+xml'
        }
        return content_types.get(extension.lower(), 'application/octet-stream')
    
    def upload_normalized_record(self,
                               schema_version: str,
                               entity_type: str,
                               record_id: str,
                               content: bytes,
                               metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload normalized structured record
        """
        try:
            now = datetime.utcnow()
            object_key = self._build_normalized_key(
                schema_version=schema_version,
                entity_type=entity_type,
                year=now.year,
                month=now.month,
                day=now.day,
                record_id=record_id
            )
            
            upload_metadata = {
                'schema_version': schema_version,
                'entity_type': entity_type,
                'record_id': record_id,
                'uploaded_at': now.isoformat(),
                'content_type': 'application/json'
            }
            
            if metadata:
                upload_metadata.update(metadata)
            
            self.client.put_object(
                Bucket=self.config.bucket_name,
                Key=object_key,
                Body=content,
                Metadata=upload_metadata,
                ContentType='application/json'
            )
            
            logger.info(f"Uploaded normalized record: {object_key}")
            
            return {
                'object_key': object_key,
                'r2_uri': f"r2://{self.config.bucket_name}/{object_key}",
                'uploaded_at': now.isoformat(),
                'metadata': upload_metadata
            }
            
        except Exception as e:
            logger.error(f"Failed to upload normalized record: {e}")
            raise
    
    def upload_export(self,
                     run_id: str,
                     dataset_name: str,
                     content: bytes,
                     metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload analytical export (e.g., Parquet)
        """
        try:
            object_key = self._build_export_key(run_id, dataset_name)
            
            upload_metadata = {
                'run_id': run_id,
                'dataset_name': dataset_name,
                'uploaded_at': datetime.utcnow().isoformat(),
                'content_type': 'application/octet-stream'
            }
            
            if metadata:
                upload_metadata.update(metadata)
            
            self.client.put_object(
                Bucket=self.config.bucket_name,
                Key=object_key,
                Body=content,
                Metadata=upload_metadata,
                ContentType='application/octet-stream'
            )
            
            logger.info(f"Uploaded export: {object_key}")
            
            return {
                'object_key': object_key,
                'r2_uri': f"r2://{self.config.bucket_name}/{object_key}",
                'metadata': upload_metadata
            }
            
        except Exception as e:
            logger.error(f"Failed to upload export: {e}")
            raise


def create_r2_client_from_env() -> R2Client:
    """
    Create R2 client from environment variables
    Required env vars:
    - R2_ACCOUNT_ID
    - R2_ACCESS_KEY_ID
    - R2_SECRET_ACCESS_KEY
    - R2_BUCKET_NAME
    """
    config = R2Config(
        account_id=os.getenv('R2_ACCOUNT_ID'),
        access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
        secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
        bucket_name=os.getenv('R2_BUCKET_NAME')
    )
    
    return R2Client(config)
