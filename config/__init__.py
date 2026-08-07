"""
Configuration management system for Festival Intelligence Terminal.
Secure, extensible configuration handling for all platform components.
"""
import os
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from cryptography.fernet import Fernet


class Environment(Enum):
    """Environment types."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ConfigCategory(Enum):
    """Configuration categories for organization."""
    DATABASE = "database"
    API_KEYS = "api_keys"
    MONID = "monid"
    STREAMING = "streaming"
    SOCIAL = "social"
    NEWS = "news"
    CONTACTS = "contacts"
    COMMUNICATION = "communication"
    VISUALIZATION = "visualization"
    LOGGING = "logging"
    MONITORING = "monitoring"


@dataclass
class APIKeyConfig:
    """Configuration for an API key."""
    name: str
    key: str
    category: ConfigCategory
    required: bool = True
    encrypted: bool = True
    description: str = ""
    rotation_days: Optional[int] = None
    last_rotated: Optional[str] = None


@dataclass
class DatabaseConfig:
    """Database configuration."""
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl_mode: str = "require"
    pool_size: int = 10
    max_overflow: int = 20


@dataclass
class MonidConfig:
    """Monid.ai configuration."""
    api_key: str
    base_url: str = "https://api.monid.ai"
    mcp_url: str = "https://mcp.monid.ai/v1"
    workspace_id: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3


@dataclass
class StreamingConfig:
    """Streaming service configurations."""
    spotify_client_id: Optional[str] = None
    spotify_client_secret: Optional[str] = None
    apple_music_key_id: Optional[str] = None
    apple_music_team_id: Optional[str] = None
    apple_music_private_key: Optional[str] = None


@dataclass
class SocialConfig:
    """Social media service configurations."""
    twitter_bearer_token: Optional[str] = None
    twitter_api_key: Optional[str] = None
    twitter_api_secret: Optional[str] = None
    instagram_access_token: Optional[str] = None
    tiktok_access_token: Optional[str] = None


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[str] = None
    max_bytes: int = 10485760  # 10MB
    backup_count: int = 5


class ConfigurationManager:
    """Centralized configuration management with security."""
    
    def __init__(self, env: Environment = Environment.DEVELOPMENT):
        self.env = env
        self.config_dir = Path(__file__).parent
        self.secrets_file = self.config_dir / "secrets.enc"
        self.config_file = self.config_dir / f"config_{env.value}.json"
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher = Fernet(self.encryption_key)
        
        # Load configurations
        self.api_keys: Dict[str, APIKeyConfig] = {}
        self.database_config: Optional[DatabaseConfig] = None
        self.monid_config: Optional[MonidConfig] = None
        self.streaming_config: Optional[StreamingConfig] = None
        self.social_config: Optional[SocialConfig] = None
        self.logging_config: Optional[LoggingConfig] = None
        
        self._load_configurations()
    
    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for secrets."""
        key_file = self.config_dir / ".encryption_key"
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            # Restrict file permissions
            os.chmod(key_file, 0o600)
            return key
    
    def _load_configurations(self):
        """Load all configurations from environment and files."""
        # Load from environment variables (primary source)
        self._load_from_environment()
        
        # Load from config file if exists
        if self.config_file.exists():
            self._load_from_file()
        
        # Load encrypted secrets if exists
        if self.secrets_file.exists():
            self._load_secrets()
    
    def _load_from_environment(self):
        """Load configuration from environment variables."""
        # Database
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = int(os.getenv("DB_PORT", "5432"))
        db_name = os.getenv("DB_NAME", "festival_intelligence")
        db_user = os.getenv("DB_USER", "postgres")
        db_pass = os.getenv("DB_PASSWORD", "")
        
        if db_host and db_user:
            self.database_config = DatabaseConfig(
                host=db_host,
                port=db_port,
                database=db_name,
                username=db_user,
                password=db_pass
            )
        
        # Monid.ai
        monid_key = os.getenv("MONID_API_KEY")
        if monid_key:
            self.monid_config = MonidConfig(
                api_key=monid_key,
                base_url=os.getenv("MONID_BASE_URL", "https://api.monid.ai"),
                mcp_url=os.getenv("MONID_MCP_URL", "https://mcp.monid.ai/v1")
            )
        
        # Streaming services
        self.streaming_config = StreamingConfig(
            spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            apple_music_key_id=os.getenv("APPLE_MUSIC_KEY_ID"),
            apple_music_team_id=os.getenv("APPLE_MUSIC_TEAM_ID"),
            apple_music_private_key=os.getenv("APPLE_MUSIC_PRIVATE_KEY")
        )
        
        # Social services
        self.social_config = SocialConfig(
            twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
            twitter_api_key=os.getenv("TWITTER_API_KEY"),
            twitter_api_secret=os.getenv("TWITTER_API_SECRET"),
            instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN"),
            tiktok_access_token=os.getenv("TIKTOK_ACCESS_TOKEN")
        )
        
        # Logging
        self.logging_config = LoggingConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            file_path=os.getenv("LOG_FILE"),
            max_bytes=int(os.getenv("LOG_MAX_BYTES", "10485760")),
            backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5"))
        )
        
        # Additional API keys
        self._register_api_key("hugging_face", os.getenv("HUGGING_FACE_KEY"), ConfigCategory.API_KEYS)
        self._register_api_key("kaggle", os.getenv("KAGGLE_KEY"), ConfigCategory.API_KEYS)
        self._register_api_key("kaggle_username", os.getenv("KAGGLE_USERNAME"), ConfigCategory.API_KEYS, encrypted=False)
        self._register_api_key("musicbrainz", os.getenv("MUSICBRAINZ_KEY"), ConfigCategory.API_KEYS)
        self._register_api_key("setlistfm", os.getenv("SETLISTFM_KEY"), ConfigCategory.API_KEYS)
        self._register_api_key("ticketmaster", os.getenv("TICKETMASTER_KEY"), ConfigCategory.API_KEYS)
        self._register_api_key("youtube", os.getenv("YOUTUBE_KEY"), ConfigCategory.API_KEYS)
    
    def _register_api_key(self, name: str, key: Optional[str], category: ConfigCategory, 
                         encrypted: bool = True, required: bool = False):
        """Register an API key configuration."""
        if key:
            self.api_keys[name] = APIKeyConfig(
                name=name,
                key=key,
                category=category,
                encrypted=encrypted,
                required=required
            )
    
    def _load_from_file(self):
        """Load configuration from JSON file."""
        try:
            with open(self.config_file, 'r') as f:
                config_data = json.load(f)
            
            # Load database config
            if 'database' in config_data:
                db_data = config_data['database']
                self.database_config = DatabaseConfig(**db_data)
            
            # Load Monid config
            if 'monid' in config_data:
                self.monid_config = MonidConfig(**config_data['monid'])
            
            # Load streaming config
            if 'streaming' in config_data:
                self.streaming_config = StreamingConfig(**config_data['streaming'])
            
            # Load social config
            if 'social' in config_data:
                self.social_config = SocialConfig(**config_data['social'])
            
            # Load logging config
            if 'logging' in config_data:
                self.logging_config = LoggingConfig(**config_data['logging'])
            
        except Exception as e:
            print(f"Error loading config file: {e}")
    
    def _load_secrets(self):
        """Load encrypted secrets from file."""
        try:
            with open(self.secrets_file, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.cipher.decrypt(encrypted_data)
            secrets = json.loads(decrypted_data.decode())
            
            # Register secrets as API keys
            for name, secret_data in secrets.items():
                self.api_keys[name] = APIKeyConfig(
                    name=name,
                    key=secret_data['key'],
                    category=ConfigCategory(secret_data['category']),
                    encrypted=True,
                    required=secret_data.get('required', True),
                    description=secret_data.get('description', '')
                )
            
        except Exception as e:
            print(f"Error loading secrets: {e}")
    
    def save_secrets(self, secrets: Dict[str, Dict[str, Any]]):
        """Save secrets to encrypted file."""
        secrets_data = {}
        
        for name, secret_info in secrets.items():
            secrets_data[name] = {
                'key': secret_info['key'],
                'category': secret_info.get('category', 'api_keys'),
                'required': secret_info.get('required', True),
                'description': secret_info.get('description', '')
            }
        
        encrypted_data = self.cipher.encrypt(json.dumps(secrets_data).encode())
        
        with open(self.secrets_file, 'wb') as f:
            f.write(encrypted_data)
        
        # Restrict file permissions
        os.chmod(self.secrets_file, 0o600)
    
    def get_api_key(self, name: str) -> Optional[str]:
        """Get API key by name."""
        api_key_config = self.api_keys.get(name)
        if api_key_config:
            return api_key_config.key
        return None
    
    def get_all_api_keys(self, category: Optional[ConfigCategory] = None) -> Dict[str, str]:
        """Get all API keys, optionally filtered by category."""
        if category:
            return {
                name: config.key 
                for name, config in self.api_keys.items() 
                if config.category == category
            }
        return {name: config.key for name, config in self.api_keys.items()}
    
    def add_api_key(self, name: str, key: str, category: ConfigCategory, 
                   encrypted: bool = True, required: bool = True, description: str = ""):
        """Add or update an API key."""
        self.api_keys[name] = APIKeyConfig(
            name=name,
            key=key,
            category=category,
            encrypted=encrypted,
            required=required,
            description=description
        )
    
    def remove_api_key(self, name: str) -> bool:
        """Remove an API key."""
        if name in self.api_keys:
            del self.api_keys[name]
            return True
        return False
    
    def validate_required_configs(self) -> List[str]:
        """Validate that all required configurations are present."""
        missing = []
        
        # Check required API keys
        for name, config in self.api_keys.items():
            if config.required and not config.key:
                missing.append(f"API key: {name}")
        
        # Check database config
        if not self.database_config:
            missing.append("Database configuration")
        
        # Check Monid config (critical for platform)
        if not self.monid_config or not self.monid_config.api_key:
            missing.append("Monid.ai configuration")
        
        return missing
    
    def get_config_summary(self) -> Dict[str, Any]:
        """Get summary of current configuration (without sensitive data)."""
        return {
            'environment': self.env.value,
            'database_configured': self.database_config is not None,
            'monid_configured': self.monid_config is not None,
            'streaming_configured': self.streaming_config is not None,
            'social_configured': self.social_config is not None,
            'api_keys_count': len(self.api_keys),
            'api_keys_categories': list(set(config.category.value for config in self.api_keys.values())),
            'logging_configured': self.logging_config is not None
        }
    
    def export_config(self, include_secrets: bool = False) -> Dict[str, Any]:
        """Export configuration to dictionary."""
        config = {
            'environment': self.env.value,
            'database': self.database_config.__dict__ if self.database_config else None,
            'monid': self.monid_config.__dict__ if self.monid_config else None,
            'streaming': self.streaming_config.__dict__ if self.streaming_config else None,
            'social': self.social_config.__dict__ if self.social_config else None,
            'logging': self.logging_config.__dict__ if self.logging_config else None,
            'api_keys': {}
        }
        
        if include_secrets:
            config['api_keys'] = {
                name: {
                    'key': config_obj.key,
                    'category': config_obj.category.value,
                    'required': config_obj.required,
                    'description': config_obj.description
                }
                for name, config_obj in self.api_keys.items()
            }
        else:
            config['api_keys'] = {
                name: {
                    'category': config_obj.category.value,
                    'required': config_obj.required,
                    'has_key': bool(config_obj.key),
                    'description': config_obj.description
                }
                for name, config_obj in self.api_keys.items()
            }
        
        return config
    
    def rotate_api_key(self, name: str, new_key: str) -> bool:
        """Rotate an API key."""
        if name in self.api_keys:
            self.api_keys[name].key = new_key
            self.api_keys[name].last_rotated = datetime.utcnow().isoformat()
            return True
        return False


# Global configuration instance
_config_manager: Optional[ConfigurationManager] = None


def get_config(env: Optional[Environment] = None) -> ConfigurationManager:
    """Get global configuration manager instance."""
    global _config_manager
    
    if _config_manager is None:
        if env is None:
            env = Environment(os.getenv("ENVIRONMENT", "development"))
        _config_manager = ConfigurationManager(env)
    
    return _config_manager


def reset_config():
    """Reset global configuration manager (useful for testing)."""
    global _config_manager
    _config_manager = None
