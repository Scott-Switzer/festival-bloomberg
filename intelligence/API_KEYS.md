# API Keys Required for Festival Intelligence Terminal

## Required for Full Functionality

### MusicBrainz
- **Required**: No API key needed
- **Setup**: Set `MUSICBRAINZ_USER_AGENT` to your email address
- **Rate Limit**: 1 request per second
- **Signup**: Not required - just use your email in user agent string
- **URL**: https://musicbrainz.org/doc/MusicBrainz_API

### setlist.fm
- **Required**: Yes, for concert history
- **Rate Limit**: Requires API key
- **Signup**: https://api.setlist.fm/docs/1.0/index.html
- **Cost**: Free for development
- **Environment Variable**: `SETLISTFM_API_KEY`

### Ticketmaster
- **Required**: Yes, for future events and venues
- **Rate Limit**: 5,000 calls daily
- **Signup**: https://developer.ticketmaster.com/
- **Cost**: Free tier available
- **Environment Variable**: `TICKETMASTER_API_KEY`

### YouTube Data API
- **Required**: Yes, for video engagement metrics
- **Rate Limit**: Quota-based (10,000 units daily default)
- **Signup**: https://console.cloud.google.com/ (create project, enable YouTube Data API v3)
- **Cost**: Free tier available
- **Environment Variable**: `YOUTUBE_API_KEY`

## Optional (Enhanced Features)

### BEA (Bureau of Economic Analysis)
- **Required**: No - for market economics data
- **Signup**: https://apps.bea.gov/api/signup/
- **Cost**: Free
- **Environment Variable**: `BEA_API_KEY`

### BLS (Bureau of Labor Statistics)
- **Required**: No - for labor market data
- **Signup**: https://api.bls.gov/publicAPI/v2/
- **Cost**: Free
- **Environment Variable**: `BLS_API_KEY`

## No API Key Required (Free Public APIs)

These work without any API key:
- **Wikimedia Pageviews**: Free, no authentication
- **GDELT**: Free, no authentication
- **NWS (National Weather Service)**: Free, no authentication
- **NOAA NCEI**: Free, no authentication
- **BTS (Bureau of Transportation Stats)**: Free, no authentication
- **Census**: Free, no authentication

## Running Without API Keys

The application can run in **demo mode** without API keys using:
- Sample festival data (built-in)
- Sample artist data (built-in)
- Mock API responses
- Placeholder predictions

This is sufficient for:
- Testing the UI
- Demonstrating the architecture
- Portfolio presentation
- Development and testing

## Minimum Setup for Demo Mode

To run without any API keys, you only need:

```bash
# No API keys required for demo mode
# The app will use built-in sample data
```

## Recommended Setup for Full Functionality

For full data collection, get these keys in order:

1. **MusicBrainz** (free, just email)
2. **setlist.fm** (free, quick signup)
3. **Ticketmaster** (free tier, quick signup)
4. **YouTube** (free tier, requires Google account)

## Signup Time Estimates

- MusicBrainz: 1 minute (just set user agent)
- setlist.fm: 5 minutes
- Ticketmaster: 10 minutes
- YouTube: 15 minutes (Google Cloud setup)
- BEA: 5 minutes
- BLS: 5 minutes

**Total for all keys**: ~40 minutes

## Current Approach

For this test run, we will use **demo mode with sample data** - no API keys required.
