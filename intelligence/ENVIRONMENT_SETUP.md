# Environment Variable Setup Guide

This document explains all environment variables required for the Festival Bloomberg implementation, how to obtain them, and what they're used for.

## Required Variables

### OPENAI_API_KEY
**Purpose:** LLM-based structured data extraction using Python Instructor  
**Used By:** `extraction/llm_extractor.py`  
**Required For:** Extracting artist, festival, lineup, agency, venue, and contact information from unstructured text

**How to Obtain:**
1. Go to https://platform.openai.com/api-keys
2. Sign in or create an OpenAI account
3. Click "Create new secret key"
4. Name your key (e.g., "Festival Bloomberg")
5. Copy the key (starts with `sk-...`)
6. Add to `.env` file: `OPENAI_API_KEY=sk-your-key-here`

**Cost Considerations:**
- GPT-4o: ~$0.005/1K input tokens, ~$0.015/1K output tokens
- GPT-4o-mini: ~$0.00015/1K input tokens, ~$0.0006/1K output tokens
- Estimated cost per extraction: $0.01-$0.10 depending on model and content length

---

### R2_ACCOUNT_ID
**Purpose:** Cloudflare R2 S3-compatible object storage  
**Used By:** `storage/r2_client.py`  
**Required For:** Storing raw scraped content, normalized records, and analytical exports

**How to Obtain:**
1. Go to https://dash.cloudflare.com/
2. Sign in or create a Cloudflare account
3. Navigate to **R2** > **Overview**
4. Your Account ID is displayed on the right side of the page
5. Copy the Account ID (32-character hex string)
6. Add to `.env` file: `R2_ACCOUNT_ID=your-account-id`

---

### R2_ACCESS_KEY_ID
**Purpose:** Cloudflare R2 API authentication  
**Used By:** `storage/r2_client.py`  
**Required For:** Authenticating with R2 for upload/download operations

**How to Obtain:**
1. In Cloudflare dashboard, navigate to **R2** > **Manage R2 API Tokens**
2. Click "Create API Token"
3. Select permissions:
   - Object Read & Write
   - Admin Read
4. Select the bucket you created (or "All R2 buckets")
5. Set TTL (recommended: 1 year)
6. Click "Create API Token"
7. Copy the Access Key ID (starts with `access_key_id_...`)
8. Add to `.env` file: `R2_ACCESS_KEY_ID=your-access-key-id`

---

### R2_SECRET_ACCESS_KEY
**Purpose:** Cloudflare R2 API authentication  
**Used By:** `storage/r2_client.py`  
**Required For:** Authenticating with R2 for upload/download operations

**How to Obtain:**
1. Created simultaneously with R2_ACCESS_KEY_ID
2. Copy the Secret Access Key (shown only once during creation)
3. Add to `.env` file: `R2_SECRET_ACCESS_KEY=your-secret-key`

**Security Note:** Keep this secret. Never commit to version control.

---

### R2_BUCKET_NAME
**Purpose:** Cloudflare R2 bucket for storing data  
**Used By:** `storage/r2_client.py`  
**Required For:** Specifying which R2 bucket to use for storage

**How to Obtain:**
1. In Cloudflare dashboard, navigate to **R2** > **Overview**
2. Click "Create bucket"
3. Enter bucket name (e.g., `festival-bloomberg`)
4. Select location (recommended: Auto)
5. Click "Create bucket"
6. Add to `.env` file: `R2_BUCKET_NAME=festival-bloomberg`

**Cost Considerations:**
- Storage: $0.015/GB/month
- Class A Operations (Write): $4.50 per million requests
- Class B Operations (Read): $0.36 per million requests
- Egress: Free (unlimited)

---

## Optional Variables

### MUSICBRAINZ_USER_AGENT
**Purpose:** MusicBrainz API identification  
**Used By:** `entity/entity_resolution.py`  
**Required For:** Artist and entity resolution via MusicBrainz

**How to Obtain:**
1. No API key required, but user agent is mandatory
2. Format: `AppName/version (contact@email.com)`
3. Example: `festival-intelligence/1.0 (admin@example.com)`
4. Add to `.env` file: `MUSICBRAINZ_USER_AGENT=festival-intelligence/1.0 (your-email@example.com)`

**Rate Limits:**
- 1 request per second
- Requires proper user agent to avoid blocking

---

### MONID_API_KEY
**Purpose:** Monid.ai managed web scraping  
**Used By:** `scraping/tiered_scraper.py` (Tier 3)  
**Required For:** Escalating to managed scraping when HTTP/Playwright fails

**How to Obtain:**
1. Go to https://monid.ai/
2. Sign up for an account
3. Navigate to API keys section
4. Generate API key
5. Add to `.env` file: `MONID_API_KEY=your-monid-key`

**Cost Considerations:**
- Pay-per-use pricing
- Used as fallback tier only

---

### HUGGINGFACE_API_KEY
**Purpose:** Hugging Face model access  
**Used By:** Optional ML model integration  
**Required For:** Advanced ML features (not currently used)

**How to Obtain:**
1. Go to https://huggingface.co/settings/tokens
2. Sign in or create account
3. Click "New token"
4. Select token type (Read or Write)
6. Add to `.env` file: `HUGGINGFACE_API_KEY=your-hf-key`

---

### KAGGLE_API_KEY
**Purpose:** Kaggle dataset access  
**Used By:** Dataset download scripts  
**Required For:** Accessing Kaggle datasets (optional)

**How to Obtain:**
1. Go to https://www.kaggle.com/settings
2. Sign in or create account
3. Scroll to "API" section
4. Click "Create New API Token"
5. Download `kaggle.json` file
6. Run setup script to configure automatically

---

### SETLISTFM_API_KEY
**Purpose:** Setlist.fm API access  
**Used By:** Setlist data integration  
**Required For:** Historical setlist data (optional)

**How to Obtain:**
1. Go to https://api.setlist.fm/docs/1.0/index.html
2. Apply for API key
3. Wait for approval
4. Add to `.env` file: `SETLISTFM_API_KEY=your-setlistfm-key`

---

### TICKETMASTER_API_KEY
**Purpose:** Ticketmaster Discovery API  
**Used By:** Ticketing data integration  
**Required For:** Ticket sales and pricing data (optional)

**How to Obtain:**
1. Go to https://developer.ticketmaster.com/
2. Sign up for developer account
3. Create application
4. Get API key
5. Add to `.env` file: `TICKETMASTER_API_KEY=your-tm-key`

---

### YOUTUBE_API_KEY
**Purpose:** YouTube Data API  
**Used By:** Video and social metrics  
**Required For:** YouTube analytics (optional)

**How to Obtain:**
1. Go to https://console.cloud.google.com/
2. Create project
3. Enable YouTube Data API v3
4. Create API credentials
5. Add to `.env` file: `YOUTUBE_API_KEY=your-youtube-key`

---

### BEA_API_KEY
**Purpose:** Bureau of Economic Analysis API  
**Used By:** Economic data integration  
**Required For:** Economic indicators (optional)

**How to Obtain:**
1. Go to https://apps.bea.gov/API/signup/
2. Sign up for API key
3. Add to `.env` file: `BEA_API_KEY=your-bea-key`

---

### BLS_API_KEY
**Purpose:** Bureau of Labor Statistics API  
**Used By:** Labor market data  
**Required For:** Employment and wage data (optional)

**How to Obtain:**
1. Go to https://www.bls.gov/developers/
2. Register for API key
3. Add to `.env` file: `BLS_API_KEY=your-bls-key`

---

## Database Configuration

### DATABASE_URL
**Purpose:** PostgreSQL connection string  
**Used By:** `database/__init__.py`  
**Required For:** Core relational warehouse

**Format:**
```
postgresql://username:password@host:port/database_name
```

**Example:**
```
postgresql://postgres:password@localhost:5432/festival_intelligence
```

**For Local Development:**
1. Install PostgreSQL locally
2. Create database: `createdb festival_intelligence`
3. Add to `.env` file with your credentials

**For Production:**
- Use managed PostgreSQL (Supabase, AWS RDS, etc.)
- Use connection pooling
- Enable SSL

---

## Application Configuration

### LOG_LEVEL
**Purpose:** Logging verbosity  
**Default:** `INFO`  
**Options:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

**Usage:**
- Development: `DEBUG` for detailed logs
- Production: `INFO` or `WARNING`

---

### ENVIRONMENT
**Purpose:** Application environment  
**Default:** `development`  
**Options:** `development`, `staging`, `production`

**Usage:**
- Controls feature flags
- Affects error reporting
- Influences caching behavior

---

## Quick Setup

### Automated Setup
Run the setup script to configure all required keys:
```bash
python scripts/setup_env.py
```

The script will:
1. Prompt for each required API key
2. Update `.env` file automatically
3. Configure Kaggle if key provided
4. Provide instructions for obtaining missing keys

### Manual Setup
1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Edit `.env` file and replace placeholder values with actual keys

3. Ensure `.env` is in `.gitignore` (it should be by default)

---

## Security Best Practices

1. **Never commit `.env` to version control**
   - `.env` is in `.gitignore` by default
   - Use `.env.example` for documentation only

2. **Rotate API keys regularly**
   - Set calendar reminders for key rotation
   - Update `.env` after rotation

3. **Use environment-specific keys**
   - Development keys for local development
   - Production keys for production deployment
   - Never use production keys in development

4. **Monitor API usage and costs**
   - Check Cloudflare R2 usage dashboard
   - Monitor OpenAI API usage
   - Set up billing alerts

5. **Limit API key permissions**
   - Only grant necessary permissions
   - Use scoped tokens when available
   - Set appropriate TTL for tokens

---

## Troubleshooting

### R2 Connection Issues
**Error:** `InvalidAccessKeyId` or `SignatureDoesNotMatch`
**Solution:** Verify R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY are correct

### OpenAI API Errors
**Error:** `Invalid API key` or `Insufficient quota`
**Solution:** Verify OPENAI_API_KEY and check OpenAI dashboard for quota

### MusicBrainz Rate Limiting
**Error:** `429 Too Many Requests`
**Solution:** Ensure MUSICBRAINZ_USER_AGENT is set correctly and implement rate limiting

### Database Connection Issues
**Error:** `connection refused` or `authentication failed`
**Solution:** Verify DATABASE_URL format and PostgreSQL is running

---

## Cost Estimation

### Monthly Cost Estimates (Production)

**Cloudflare R2:**
- Storage: 100 GB = $1.50/month
- Operations: 1M writes = $4.50/month
- Operations: 10M reads = $3.60/month
- **Total:** ~$10/month

**OpenAI API:**
- 10,000 extractions @ GPT-4o-mini = $7.50/month
- 1,000 extractions @ GPT-4o = $20.00/month
- **Total:** $7.50-$20/month depending on model

**PostgreSQL:**
- Managed instance (e.g., Supabase): $25/month
- **Total:** $25/month

**Estimated Total:** ~$40-60/month for production

---

## Support

For issues with:
- **Cloudflare R2:** https://developers.cloudflare.com/r2/
- **OpenAI API:** https://platform.openai.com/docs
- **MusicBrainz:** https://musicbrainz.org/doc/MusicBrainz_API
- **PostgreSQL:** https://www.postgresql.org/docs/

For Festival Bloomberg implementation issues, refer to:
- `FESTIVAL_BLOOMBERG_IMPLEMENTATION.md`
- `QUALITY_TEST_REPORT.md`
