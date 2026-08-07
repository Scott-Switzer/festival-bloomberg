# Configuration Management System

## Overview
Secure, extensible configuration management for the Festival Intelligence Terminal. Handles API keys, database connections, and platform settings with encryption and validation.

## Features

### Security
- **Encryption**: All sensitive API keys are encrypted using Fernet symmetric encryption
- **File Permissions**: Encrypted files are automatically set to 600 (owner read/write only)
- **Environment Variables**: Primary configuration source for sensitive data
- **Validation**: Required configuration validation before startup

### Flexibility
- **Multiple Environments**: Development, Staging, Production configurations
- **Extensible Categories**: Easy to add new configuration categories
- **Multiple Sources**: Environment variables, JSON config files, encrypted secrets
- **API Key Rotation**: Built-in key rotation support

### Organization
- **Categorized Configuration**: Database, API Keys, Monid, Streaming, Social, etc.
- **Configuration Summary**: Overview of what's configured and what's missing
- **Export/Import**: Configuration export for backup/migration

## Usage

### Basic Usage

```python
from config import get_config, Environment

# Get configuration manager (uses ENVIRONMENT env var or defaults to development)
config = get_config()

# Get specific API key
monid_key = config.get_api_key("monid")

# Get all API keys in a category
streaming_keys = config.get_all_api_keys(ConfigCategory.STREAMING)

# Access database configuration
db_config = config.database_config
print(f"Connecting to {db_config.host}:{db_config.port}/{db_config.database}")
```

### Setup Configuration

Run the interactive setup script:

```bash
python scripts/setup_config.py
```

This will guide you through:
1. Environment selection (development/staging/production)
2. Database configuration
3. Monid.ai configuration (required)
4. Streaming services (optional)
5. Social media (optional)
6. Additional API keys (optional)
7. Logging configuration

The script generates a `.env` file with all your configurations.

### Environment Variables

The configuration system prioritizes environment variables. Set these in your `.env` file:

#### Required
```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=festival_intelligence
DB_USER=postgres
DB_PASSWORD=your_password

# Monid.ai (Critical)
MONID_API_KEY=your_monid_key
MONID_BASE_URL=https://api.monid.ai
MONID_MCP_URL=https://mcp.monid.ai/v1
```

#### Optional - Streaming
```bash
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
APPLE_MUSIC_KEY_ID=your_key_id
APPLE_MUSIC_TEAM_ID=your_team_id
APPLE_MUSIC_PRIVATE_KEY=your_private_key
```

#### Optional - Social Media
```bash
TWITTER_BEARER_TOKEN=your_token
TWITTER_API_KEY=your_api_key
TWITTER_API_SECRET=your_api_secret
INSTAGRAM_ACCESS_TOKEN=your_token
TIKTOK_ACCESS_TOKEN=your_token
```

#### Optional - Additional APIs
```bash
HUGGING_FACE_KEY=your_key
KAGGLE_KEY=your_key
KAGGLE_USERNAME=your_username
MUSICBRAINZ_KEY=your_key
SETLISTFM_KEY=your_key
TICKETMASTER_KEY=your_key
YOUTUBE_KEY=your_key
```

#### Logging
```bash
LOG_LEVEL=INFO
LOG_FILE=logs/festival_intelligence.log
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

### Configuration Files

For non-sensitive configuration, you can use JSON files:

**config/config_development.json**
```json
{
  "database": {
    "host": "localhost",
    "port": 5432,
    "database": "festival_intelligence",
    "username": "postgres",
    "password": "",
    "ssl_mode": "prefer"
  },
  "logging": {
    "level": "INFO",
    "file_path": "logs/festival_intelligence.log"
  }
}
```

### Encrypted Secrets

For highly sensitive data, use encrypted secrets:

```python
from config import get_config

config = get_config()

# Save secrets to encrypted file
secrets = {
    "production_db": {
        "key": "super_secret_password",
        "category": "database",
        "required": True,
        "description": "Production database password"
    }
}
config.save_secrets(secrets)

# Secrets are automatically loaded on startup
```

### Validation

Check if all required configurations are present:

```python
from config import get_config

config = get_config()
missing = config.validate_required_configs()

if missing:
    print("Missing configurations:", missing)
else:
    print("All required configurations present")
```

### Configuration Summary

Get an overview of current configuration:

```python
from config import get_config

config = get_config()
summary = config.get_config_summary()

print(summary)
# Output:
# {
#   'environment': 'development',
#   'database_configured': True,
#   'monid_configured': True,
#   'streaming_configured': False,
#   'social_configured': False,
#   'api_keys_count': 5,
#   'api_keys_categories': ['api_keys', 'monid'],
#   'logging_configured': True
# }
```

### API Key Management

Add or update API keys programmatically:

```python
from config import get_config, ConfigCategory

config = get_config()

# Add new API key
config.add_api_key(
    name="new_service",
    key="api_key_value",
    category=ConfigCategory.API_KEYS,
    encrypted=True,
    required=True,
    description="New service API key"
)

# Rotate existing key
config.rotate_api_key("monid", "new_monid_key")

# Remove API key
config.remove_api_key("old_service")
```

### Export Configuration

Export configuration for backup or migration:

```python
from config import get_config

config = get_config()

# Export without secrets (safe for sharing)
safe_export = config.export_config(include_secrets=False)

# Export with secrets (for backup)
full_export = config.export_config(include_secrets=True)
```

## Security Best Practices

1. **Never commit .env files** - Add `.env` to `.gitignore`
2. **Never commit encryption keys** - The `.encryption_key` file is auto-generated and should be in `.gitignore`
3. **Never commit secrets.enc** - Encrypted secrets file should be in `.gitignore`
4. **Use environment variables** - Primary source for sensitive data
5. **Rotate keys regularly** - Use `rotate_api_key()` method
6. **Limit file permissions** - Sensitive files are automatically set to 600
7. **Use different keys per environment** - Development, staging, production should have separate keys

## Adding New Configuration Categories

To add a new configuration category:

1. Add to `ConfigCategory` enum:
```python
class ConfigCategory(Enum):
    # ... existing categories
    NEW_CATEGORY = "new_category"
```

2. Create configuration dataclass:
```python
@dataclass
class NewConfig:
    """Configuration for new service."""
    api_key: str
    endpoint: str = "https://api.example.com"
    timeout: int = 30
```

3. Add to ConfigurationManager:
```python
class ConfigurationManager:
    def __init__(self):
        # ... existing initialization
        self.new_config: Optional[NewConfig] = None
        self._load_configurations()
```

4. Add loading logic in `_load_from_environment`:
```python
new_key = os.getenv("NEW_SERVICE_KEY")
if new_key:
    self.new_config = NewConfig(
        api_key=new_key,
        endpoint=os.getenv("NEW_SERVICE_ENDPOINT", "https://api.example.com")
    )
```

## File Structure

```
config/
├── __init__.py              # Main configuration management
├── config_development.json  # Development config template
├── config_staging.json      # Staging config template
├── config_production.json   # Production config template
├── secrets.enc             # Encrypted secrets (auto-generated)
└── .encryption_key         # Encryption key (auto-generated)
```

## Troubleshooting

### Configuration not loading
- Check that `.env` file exists in project root
- Verify environment variable names match exactly
- Check file permissions on encrypted files

### Encryption errors
- Ensure `.encryption_key` file exists
- Check file permissions (should be 600)
- Verify cryptography package is installed

### Missing required configurations
- Run `python scripts/setup_config.py` to configure
- Check `.env` file for missing values
- Verify environment variables are set

## Dependencies

```bash
pip install cryptography
```

## MVP Notes

For MVP stage:
- Authentication/authorization not required
- Focus on core configuration (database, Monid.ai)
- Optional configurations (streaming, social) can be added later
- Environment-based configuration sufficient for now
- No need for complex secret management services yet
