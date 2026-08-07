# Festival Scraper - Scrapy Web Scraping Framework

Scrapy-based web scraping framework for the Festival Intelligence Terminal. This project collects data from festival websites, social media platforms, and artist metrics sources.

## Installation

Scrapy is already installed in the project. If you need to reinstall:

```bash
pip install scrapy
```

Optional: Install TextBlob for sentiment analysis:

```bash
pip install textblob
python -m textblob.download_corpora
```

## Project Structure

```
festival_scraper/
├── festival_scraper/
│   ├── __init__.py
│   ├── items.py              # Data item definitions
│   middlewares.py           # Custom middlewares
│   pipelines.py             # Data processing pipelines
│   settings.py             # Scrapy settings
│   └── spiders/             # Spider modules
│       ├── __init__.py
│       ├── festival_lineup.py  # Festival lineup scraper
│       └── social_media.py     # Social media scraper
├── scrapy.cfg               # Project configuration
└── README.md               # This file
```

## Available Spiders

### 1. Festival Lineup Spider (`festival_lineup`)

Scrapes festival lineup data from festival websites.

**Usage:**
```bash
cd festival_scraper
scrapy crawl festival_lineup
```

**Features:**
- Extracts artist names, stages, timeslots, dates, years, days, genres
- Supports multiple festival websites
- Configurable download delays and concurrency
- Respects robots.txt

**Customization:**
Edit `spiders/festival_lineup.py` to add new festival websites or modify CSS selectors.

### 2. Glastonbury Spider (`glastonbury`)

Specialized spider for Glastonbury Festival lineup data.

**Usage:**
```bash
cd festival_scraper
scrapy crawl glastonbury
```

### 3. Social Media Spider (`social_media`)

Scrapes social media sentiment data for artists.

**Usage:**
```bash
cd festival_scraper
scrapy crawl social_media -a artist_name="artistname" -a platform="instagram"
```

**Parameters:**
- `artist_name` (required): Artist's username on the platform
- `platform` (optional): Platform to scrape (instagram, twitter, x). Default: instagram

**Example:**
```bash
scrapy crawl social_media -a artist_name="badbunny" -a platform="instagram"
```

**Note:** Social media scraping may require authentication and handling of dynamic content. The current implementation is a template that needs customization for actual use.

## Data Items

### FestivalLineupItem
- `artist_name`: Artist/band name
- `stage`: Festival stage name
- `timeslot`: Performance time
- `date`: Performance date
- `year`: Festival year
- `day`: Festival day
- `weekend`: Festival weekend (if applicable)
- `genre`: Music genre
- `festival_name`: Festival name
- `scraped_at`: Timestamp of scraping

### SocialMediaItem
- `artist_name`: Artist name
- `platform`: Social media platform
- `followers`: Follower count
- `engagement_rate`: Engagement rate
- `sentiment_score`: Sentiment score (-1 to 1)
- `sentiment_polarity`: Sentiment classification (positive/negative/neutral)
- `post_text`: Post text/caption
- `likes`: Like count
- `comments`: Comment count
- `shares`: Share count
- `scraped_at`: Timestamp of scraping

### ArtistMetricsItem
- `artist_name`: Artist name
- `spotify_followers`: Spotify follower count
- `spotify_popularity`: Spotify popularity score (0-100)
- `monthly_listeners`: Monthly listener count
- `instagram_followers`: Instagram follower count
- `twitter_followers`: Twitter follower count
- `youtube_subscribers`: YouTube subscriber count
- `tiktok_followers`: TikTok follower count
- `scraped_at`: Timestamp of scraping

## Pipelines

### Data Validation Pipeline (Priority: 100)
Validates required fields and data quality:
- Checks for required fields based on item type
- Validates data types (converts follower counts to integers)
- Logs warnings for missing or invalid data

### Sentiment Analysis Pipeline (Priority: 200)
Performs sentiment analysis on social media posts:
- Uses TextBlob for sentiment analysis
- Calculates sentiment polarity (-1 to 1)
- Classifies sentiment as positive/negative/neutral
- Requires TextBlob to be installed

### Deduplication Pipeline (Priority: 300)
Removes duplicate items:
- Creates unique keys based on item type
- Prevents duplicate data in output files
- Logs skipped duplicates

### Festival Scraper Pipeline (Priority: 400)
Saves scraped data to JSONL files:
- Outputs to `warehouse/scraped/` directory
- Creates timestamped files per spider run
- Uses JSONL format (one JSON object per line)

## Output

Scraped data is saved to `warehouse/scraped/` directory:

```
warehouse/scraped/
├── festival_lineup_2024-08-03T21:00:00.jsonl
├── glastonbury_2024-08-03T21:05:00.jsonl
└── social_media_2024-08-03T21:10:00.jsonl
```

Each line in the JSONL file is a complete JSON object representing one scraped item.

## Configuration

### Settings (`settings.py`)

Key settings configured:
- `ROBOTSTXT_OBEY`: True (respects robots.txt)
- `CONCURRENT_REQUESTS_PER_DOMAIN`: 1 (conservative default)
- `DOWNLOAD_DELAY`: 1 second (conservative default)
- `ITEM_PIPELINES`: All pipelines enabled

### Custom Settings per Spider

Each spider can override settings with `custom_settings`:

```python
custom_settings = {
    'USER_AGENT': 'Custom User Agent',
    'DOWNLOAD_DELAY': 3,
    'CONCURRENT_REQUESTS': 8,
}
```

## Running Spiders

### Basic Usage
```bash
cd festival_scraper
scrapy crawl <spider_name>
```

### With Parameters
```bash
scrapy crawl social_media -a artist_name="artistname" -a platform="twitter"
```

### Output to File
```bash
scrapy crawl festival_lineup -o output.json
scrapy crawl festival_lineup -o output.csv
```

### With Logging
```bash
scrapy crawl festival_lineup --loglevel=INFO
scrapy crawl festival_lineup --logfile=scraper.log
```

## Adding New Spiders

1. Create a new spider file in `spiders/` directory:
```bash
cd festival_scraper
scrapy genspider myspider example.com
```

2. Edit the spider to implement your scraping logic:
```python
import scrapy
from festival_scraper.items import FestivalLineupItem

class MySpider(scrapy.Spider):
    name = "myspider"
    allowed_domains = ["example.com"]
    start_urls = ["https://example.com"]
    
    def parse(self, response):
        # Your scraping logic here
        pass
```

3. Define items in `items.py` if needed

4. Add custom pipeline logic in `pipelines.py` if needed

5. Update `settings.py` to enable new pipelines

## Best Practices

1. **Respect robots.txt**: Always obey robots.txt rules
2. **Use appropriate delays**: Set `DOWNLOAD_DELAY` to avoid overwhelming servers
3. **Monitor logs**: Check logs for errors and warnings
4. **Test incrementally**: Start with small scopes and expand
5. **Handle errors**: Implement error handling in your spiders
6. **Validate data**: Use the validation pipeline to ensure data quality
7. **Deduplicate**: Use the deduplication pipeline to avoid duplicates
8. **Rate limiting**: Be considerate of server resources

## Troubleshooting

### Spider Not Running
- Check that you're in the `festival_scraper` directory
- Verify spider name is correct
- Check for syntax errors in spider file

### No Data Scraped
- Verify CSS selectors are correct for target website
- Check if website requires JavaScript (may need Selenium/Playwright)
- Review logs for errors
- Test selectors in browser developer tools

### 403/Forbidden Errors
- Check robots.txt compliance
- Verify User-Agent is not blocked
- Consider adding authentication if required
- Reduce request rate

### Sentiment Analysis Not Working
- Install TextBlob: `pip install textblob`
- Download corpora: `python -m textblob.download_corpora`
- Check that post_text field is populated

## Integration with Festival Intelligence Terminal

The scraped data can be integrated into the Festival Intelligence Terminal:

1. **Load Data**: Read JSONL files from `warehouse/scraped/`
2. **Process Data**: Use the data for artist metrics, sentiment analysis
3. **Update Database**: Import scraped data into your database
4. **Real-time Updates**: Schedule regular spider runs for fresh data

Example Python code to load scraped data:

```python
import json

def load_scraped_data(filepath):
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            data.append(json.loads(line))
    return data

# Load festival lineup data
lineup_data = load_scraped_data('warehouse/scraped/festival_lineup_*.jsonl')

# Load social media data
social_data = load_scraped_data('warehouse/scraped/social_media_*.jsonl')
```

## Scheduling Regular Runs

Use cron or a task scheduler to run spiders regularly:

```bash
# Run festival lineup scraper daily at 2 AM
0 2 * * * cd /path/to/festival_scraper && scrapy crawl festival_lineup

# Run social media scraper weekly
0 3 * * 0 cd /path/to/festival_scraper && scrapy crawl social_media -a artist_name="artistname"
```

## License

BSD-3-Clause (same as Scrapy)

## Contributing

When adding new spiders or features:
1. Follow the existing code structure
2. Add documentation for new spiders
3. Test thoroughly before committing
4. Update this README with new functionality
