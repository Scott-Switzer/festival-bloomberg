"""
Setup script to add API keys to .env file for Festival Bloomberg.
"""
import os
from pathlib import Path

project_root = Path(__file__).parent.parent
env_file = project_root / ".env"

print("Festival Intelligence Terminal - API Key Setup")
print("=" * 60)
print("\nFestival Bloomberg Implementation")
print("=" * 60)

# Festival Bloomberg API Keys
print("\n--- Festival Bloomberg Required Keys ---")
nvidia_key = input("Enter your NVIDIA API key (for LLM extraction - FREE for developers): ").strip()
r2_account_id = input("Enter your Cloudflare R2 Account ID: ").strip()
r2_access_key = input("Enter your Cloudflare R2 Access Key ID: ").strip()
r2_secret_key = input("Enter your Cloudflare R2 Secret Access Key: ").strip()
r2_bucket = input("Enter your Cloudflare R2 Bucket Name: ").strip()

# Optional keys
print("\n--- Optional Keys ---")
monid_key = input("Enter your Monid.ai API key (optional, press Enter to skip): ").strip()
huggingface_key = input("Enter your Hugging Face API key (optional, press Enter to skip): ").strip()
kaggle_key = input("Enter your Kaggle API key (optional, press Enter to skip): ").strip()

# MusicBrainz user agent
musicbrainz_user_agent = input("Enter your MusicBrainz user agent (format: 'AppName/version (contact@email.com)', optional): ").strip()

# Read existing .env file
env_content = ""
if env_file.exists():
    with open(env_file, 'r') as f:
        env_content = f.read()

# Update or add keys
lines = env_content.split('\n')
updated_lines = []
keys_to_update = {
    'NVIDIA_API_KEY': nvidia_key,
    'R2_ACCOUNT_ID': r2_account_id,
    'R2_ACCESS_KEY_ID': r2_access_key,
    'R2_SECRET_ACCESS_KEY': r2_secret_key,
    'R2_BUCKET_NAME': r2_bucket,
    'MONID_API_KEY': monid_key,
    'HUGGINGFACE_API_KEY': huggingface_key,
    'KAGGLE_API_KEY': kaggle_key,
    'MUSICBRAINZ_USER_AGENT': musicbrainz_user_agent
}

# Process existing lines
for line in lines:
    if line.strip() and not line.strip().startswith('#'):
        key = line.split('=')[0] if '=' in line else None
        if key and key in keys_to_update:
            # Skip this line, will be added later
            continue
    updated_lines.append(line)

# Add new keys
for key, value in keys_to_update.items():
    if value:  # Only add if value is provided
        updated_lines.append(f"{key}={value}")

# Write back to .env
with open(env_file, 'w') as f:
    f.write('\n'.join(updated_lines))

print("\n✓ API keys added to .env file")
print(f"✓ Location: {env_file}")

# Setup Kaggle if key provided
if kaggle_key:
    print("\nSetting up Kaggle API...")
    kaggle_dir = Path.home() / ".kaggle"
    kaggle_dir.mkdir(exist_ok=True)
    kaggle_json = kaggle_dir / "kaggle.json"
    
    import json
    with open(kaggle_json, 'w') as f:
        json.dump({"username": kaggle_key, "key": kaggle_key}, f)
    
    os.chmod(kaggle_json, 0o600)
    print("✓ Kaggle API configured")

print("\nSetup complete!")
print("\nTo get NVIDIA API key (FREE for developers):")
print("1. Go to https://build.nvidia.com/")
print("2. Sign up for NVIDIA Developer Program (FREE)")
print("3. Navigate to Settings > API Keys")
print("4. Generate API key")
print("5. Copy the key (starts with 'nvapi-')")
print("\nNVIDIA provides FREE access to:")
print("- Llama 3.1 8B Instruct")
print("- Mistral 7B Instruct")
print("- Mixtral 8x7B Instruct")
print("- Nemotron 4 340B Instruct")
print("- And 100+ more models")
print("\nTo get Cloudflare R2 credentials:")
print("1. Go to https://dash.cloudflare.com/")
print("2. Navigate to R2 > Overview")
print("3. Create an R2 bucket")
print("4. Go to R2 > Manage R2 API Tokens")
print("5. Create an API token with R2 permissions")
print("6. Copy Account ID, Access Key ID, and Secret Access Key")
