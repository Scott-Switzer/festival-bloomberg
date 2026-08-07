"""
Configuration setup script for Festival Intelligence Terminal.
Interactive script to set up environment variables and API keys securely.
"""
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config import ConfigurationManager, Environment, ConfigCategory


def print_header(text: str):
    """Print formatted header."""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def print_section(text: str):
    """Print formatted section."""
    print(f"\n{text}")
    print("-" * 40)


def get_input(prompt: str, required: bool = True, default: str = "") -> str:
    """Get user input with optional default."""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    
    while True:
        value = input(prompt).strip()
        
        if not value:
            if default:
                return default
            elif required:
                print("This field is required.")
                continue
            else:
                return ""
        
        return value


def setup_database_config(config_manager: ConfigurationManager):
    """Set up database configuration."""
    print_section("Database Configuration")
    
    print("Enter your PostgreSQL database details:")
    db_host = get_input("Database host", default="localhost")
    db_port = get_input("Database port", default="5432")
    db_name = get_input("Database name", default="festival_intelligence")
    db_user = get_input("Database username", default="postgres")
    db_pass = get_input("Database password", required=False)
    
    # Set environment variables
    os.environ["DB_HOST"] = db_host
    os.environ["DB_PORT"] = db_port
    os.environ["DB_NAME"] = db_name
    os.environ["DB_USER"] = db_user
    if db_pass:
        os.environ["DB_PASSWORD"] = db_pass
    
    print("✓ Database configuration saved")


def setup_monid_config(config_manager: ConfigurationManager):
    """Set up Monid.ai configuration."""
    print_section("Monid.ai Configuration")
    
    monid_key = get_input("Monid.ai API key", required=True)
    base_url = get_input("Monid.ai base URL", default="https://api.monid.ai")
    mcp_url = get_input("Monid.ai MCP URL", default="https://mcp.monid.ai/v1")
    
    # Set environment variables
    os.environ["MONID_API_KEY"] = monid_key
    os.environ["MONID_BASE_URL"] = base_url
    os.environ["MONID_MCP_URL"] = mcp_url
    
    print("✓ Monid.ai configuration saved")


def setup_streaming_config(config_manager: ConfigurationManager):
    """Set up streaming service configuration."""
    print_section("Streaming Services Configuration (Optional)")
    
    print("Spotify Configuration:")
    spotify_client_id = get_input("Spotify Client ID", required=False)
    spotify_client_secret = get_input("Spotify Client Secret", required=False)
    
    print("\nApple Music Configuration:")
    apple_music_key_id = get_input("Apple Music Key ID", required=False)
    apple_music_team_id = get_input("Apple Music Team ID", required=False)
    apple_music_private_key = get_input("Apple Music Private Key", required=False)
    
    # Set environment variables
    if spotify_client_id:
        os.environ["SPOTIFY_CLIENT_ID"] = spotify_client_id
    if spotify_client_secret:
        os.environ["SPOTIFY_CLIENT_SECRET"] = spotify_client_secret
    if apple_music_key_id:
        os.environ["APPLE_MUSIC_KEY_ID"] = apple_music_key_id
    if apple_music_team_id:
        os.environ["APPLE_MUSIC_TEAM_ID"] = apple_music_team_id
    if apple_music_private_key:
        os.environ["APPLE_MUSIC_PRIVATE_KEY"] = apple_music_private_key
    
    print("✓ Streaming services configuration saved")


def setup_social_config(config_manager: ConfigurationManager):
    """Set up social media configuration."""
    print_section("Social Media Configuration (Optional)")
    
    print("Twitter Configuration:")
    twitter_bearer_token = get_input("Twitter Bearer Token", required=False)
    twitter_api_key = get_input("Twitter API Key", required=False)
    twitter_api_secret = get_input("Twitter API Secret", required=False)
    
    print("\nInstagram Configuration:")
    instagram_access_token = get_input("Instagram Access Token", required=False)
    
    print("\nTikTok Configuration:")
    tiktok_access_token = get_input("TikTok Access Token", required=False)
    
    # Set environment variables
    if twitter_bearer_token:
        os.environ["TWITTER_BEARER_TOKEN"] = twitter_bearer_token
    if twitter_api_key:
        os.environ["TWITTER_API_KEY"] = twitter_api_key
    if twitter_api_secret:
        os.environ["TWITTER_API_SECRET"] = twitter_api_secret
    if instagram_access_token:
        os.environ["INSTAGRAM_ACCESS_TOKEN"] = instagram_access_token
    if tiktok_access_token:
        os.environ["TIKTOK_ACCESS_TOKEN"] = tiktok_access_token
    
    print("✓ Social media configuration saved")


def setup_additional_api_keys(config_manager: ConfigurationManager):
    """Set up additional API keys."""
    print_section("Additional API Keys (Optional)")
    
    hugging_face_key = get_input("Hugging Face API Key", required=False)
    kaggle_key = get_input("Kaggle API Key", required=False)
    kaggle_username = get_input("Kaggle Username", required=False)
    musicbrainz_key = get_input("MusicBrainz API Key", required=False)
    setlistfm_key = get_input("Setlist.fm API Key", required=False)
    ticketmaster_key = get_input("Ticketmaster API Key", required=False)
    youtube_key = get_input("YouTube API Key", required=False)
    
    # Set environment variables
    if hugging_face_key:
        os.environ["HUGGING_FACE_KEY"] = hugging_face_key
    if kaggle_key:
        os.environ["KAGGLE_KEY"] = kaggle_key
    if kaggle_username:
        os.environ["KAGGLE_USERNAME"] = kaggle_username
    if musicbrainz_key:
        os.environ["MUSICBRAINZ_KEY"] = musicbrainz_key
    if setlistfm_key:
        os.environ["SETLISTFM_KEY"] = setlistfm_key
    if ticketmaster_key:
        os.environ["TICKETMASTER_KEY"] = ticketmaster_key
    if youtube_key:
        os.environ["YOUTUBE_KEY"] = youtube_key
    
    print("✓ Additional API keys saved")


def setup_logging_config(config_manager: ConfigurationManager):
    """Set up logging configuration."""
    print_section("Logging Configuration")
    
    log_level = get_input("Log level (DEBUG, INFO, WARNING, ERROR)", default="INFO")
    log_file = get_input("Log file path", default="logs/festival_intelligence.log")
    
    # Set environment variables
    os.environ["LOG_LEVEL"] = log_level
    os.environ["LOG_FILE"] = log_file
    
    print("✓ Logging configuration saved")


def generate_env_file():
    """Generate .env file from current environment variables."""
    env_file = project_root / ".env"
    
    env_vars = [
        "# Database Configuration",
        f"DB_HOST={os.getenv('DB_HOST', 'localhost')}",
        f"DB_PORT={os.getenv('DB_PORT', '5432')}",
        f"DB_NAME={os.getenv('DB_NAME', 'festival_intelligence')}",
        f"DB_USER={os.getenv('DB_USER', 'postgres')}",
        f"DB_PASSWORD={os.getenv('DB_PASSWORD', '')}",
        "",
        "# Monid.ai Configuration",
        f"MONID_API_KEY={os.getenv('MONID_API_KEY', '')}",
        f"MONID_BASE_URL={os.getenv('MONID_BASE_URL', 'https://api.monid.ai')}",
        f"MONID_MCP_URL={os.getenv('MONID_MCP_URL', 'https://mcp.monid.ai/v1')}",
        "",
        "# Streaming Services",
        f"SPOTIFY_CLIENT_ID={os.getenv('SPOTIFY_CLIENT_ID', '')}",
        f"SPOTIFY_CLIENT_SECRET={os.getenv('SPOTIFY_CLIENT_SECRET', '')}",
        f"APPLE_MUSIC_KEY_ID={os.getenv('APPLE_MUSIC_KEY_ID', '')}",
        f"APPLE_MUSIC_TEAM_ID={os.getenv('APPLE_MUSIC_TEAM_ID', '')}",
        f"APPLE_MUSIC_PRIVATE_KEY={os.getenv('APPLE_MUSIC_PRIVATE_KEY', '')}",
        "",
        "# Social Media",
        f"TWITTER_BEARER_TOKEN={os.getenv('TWITTER_BEARER_TOKEN', '')}",
        f"TWITTER_API_KEY={os.getenv('TWITTER_API_KEY', '')}",
        f"TWITTER_API_SECRET={os.getenv('TWITTER_API_SECRET', '')}",
        f"INSTAGRAM_ACCESS_TOKEN={os.getenv('INSTAGRAM_ACCESS_TOKEN', '')}",
        f"TIKTOK_ACCESS_TOKEN={os.getenv('TIKTOK_ACCESS_TOKEN', '')}",
        "",
        "# Additional API Keys",
        f"HUGGING_FACE_KEY={os.getenv('HUGGING_FACE_KEY', '')}",
        f"KAGGLE_KEY={os.getenv('KAGGLE_KEY', '')}",
        f"KAGGLE_USERNAME={os.getenv('KAGGLE_USERNAME', '')}",
        f"MUSICBRAINZ_KEY={os.getenv('MUSICBRAINZ_KEY', '')}",
        f"SETLISTFM_KEY={os.getenv('SETLISTFM_KEY', '')}",
        f"TICKETMASTER_KEY={os.getenv('TICKETMASTER_KEY', '')}",
        f"YOUTUBE_KEY={os.getenv('YOUTUBE_KEY', '')}",
        "",
        "# Logging",
        f"LOG_LEVEL={os.getenv('LOG_LEVEL', 'INFO')}",
        f"LOG_FILE={os.getenv('LOG_FILE', 'logs/festival_intelligence.log')}",
        "",
        "# Environment",
        f"ENVIRONMENT={os.getenv('ENVIRONMENT', 'development')}"
    ]
    
    with open(env_file, 'w') as f:
        f.write('\n'.join(env_vars))
    
    # Restrict file permissions
    os.chmod(env_file, 0o600)
    
    print(f"\n✓ .env file generated at {env_file}")
    print("⚠  Make sure to add .env to your .gitignore file!")


def main():
    """Main configuration setup."""
    print_header("Festival Intelligence Terminal - Configuration Setup")
    
    print("This script will help you configure the Festival Intelligence Terminal.")
    print("All sensitive information will be stored securely.")
    
    # Get environment
    print_section("Environment Selection")
    print("Select your environment:")
    print("1. Development")
    print("2. Staging")
    print("3. Production")
    
    env_choice = get_input("Environment (1-3)", default="1")
    env_map = {"1": Environment.DEVELOPMENT, "2": Environment.STAGING, "3": Environment.PRODUCTION}
    env = env_map.get(env_choice, Environment.DEVELOPMENT)
    
    os.environ["ENVIRONMENT"] = env.value
    
    # Initialize configuration manager
    config_manager = ConfigurationManager(env)
    
    # Setup configurations
    setup_database_config(config_manager)
    setup_monid_config(config_manager)
    
    print("\n" + "=" * 60)
    print("Optional Configurations")
    print("=" * 60)
    
    optional_setup = get_input("Configure streaming services? (y/n)", default="n")
    if optional_setup.lower() == 'y':
        setup_streaming_config(config_manager)
    
    optional_setup = get_input("Configure social media? (y/n)", default="n")
    if optional_setup.lower() == 'y':
        setup_social_config(config_manager)
    
    optional_setup = get_input("Configure additional API keys? (y/n)", default="n")
    if optional_setup.lower() == 'y':
        setup_additional_api_keys(config_manager)
    
    setup_logging_config(config_manager)
    
    # Generate .env file
    print_section("Generating .env File")
    generate_env_file()
    
    # Validate configuration
    print_section("Configuration Validation")
    missing = config_manager.validate_required_configs()
    
    if missing:
        print("⚠  Missing required configurations:")
        for item in missing:
            print(f"  - {item}")
    else:
        print("✓ All required configurations are present")
    
    # Show configuration summary
    print_section("Configuration Summary")
    summary = config_manager.get_config_summary()
    
    for key, value in summary.items():
        print(f"{key}: {value}")
    
    print_header("Configuration Complete")
    print("Your configuration has been saved to .env file.")
    print("You can now run the Festival Intelligence Terminal.")
    print("\nNext steps:")
    print("1. Review and update .env file if needed")
    print("2. Set up your database")
    print("3. Run database migrations")
    print("4. Start the application")


if __name__ == "__main__":
    main()
