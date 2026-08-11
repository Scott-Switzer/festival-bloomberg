import scrapy
from festival_scraper.items import SocialMediaItem


class SocialMediaSpider(scrapy.Spider):
    """
    Spider for scraping social media sentiment data for artists.
    This can be adapted for different platforms (Instagram, Twitter, etc.)
    """
    name = "social_media"
    allowed_domains = ["instagram.com", "twitter.com", "x.com"]
    
    custom_settings = {
        'USER_AGENT': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'ROBOTSTXT_OBEY': True,
        'DOWNLOAD_DELAY': 5,
        'CONCURRENT_REQUESTS': 4,
    }
    
    def __init__(self, artist_name=None, platform='instagram'):
        self.artist_name = artist_name
        self.platform = platform
        super().__init__()
    
    def start_requests(self):
        """
        Generate initial requests based on artist name and platform.
        """
        if not self.artist_name:
            self.logger.error("artist_name parameter is required")
            return
        
        if self.platform == 'instagram':
            url = f"https://www.instagram.com/{self.artist_name}/"
        elif self.platform in ['twitter', 'x']:
            url = f"https://twitter.com/{self.artist_name}"
        else:
            self.logger.error(f"Unsupported platform: {self.platform}")
            return
        
        yield scrapy.Request(url, callback=self.parse_profile)
    
    def parse_profile(self, response):
        """
        Parse social media profile and extract engagement metrics.
        Note: This is a template - actual implementation may need authentication
        and handling of dynamic content.
        """
        item = SocialMediaItem()
        item['artist_name'] = self.artist_name
        item['platform'] = self.platform
        item['scraped_at'] = scrapy.utils.project.get_project_settings().get('SCRAPE_TIMESTAMP')
        
        # Extract followers (this is template code - actual selectors depend on platform)
        if self.platform == 'instagram':
            item['followers'] = self._extract_instagram_followers(response)
        elif self.platform in ['twitter', 'x']:
            item['followers'] = self._extract_twitter_followers(response)
        
        # Calculate engagement rate (template)
        item['engagement_rate'] = self._calculate_engagement_rate(response)
        
        # Extract recent posts for sentiment analysis
        post_urls = self._extract_post_urls(response)
        for url in post_urls[:10]:  # Limit to 10 most recent posts
            yield scrapy.Request(url, callback=self.parse_post, meta={'item': item})
    
    def parse_post(self, response):
        """
        Parse individual post for sentiment analysis.
        """
        item = response.meta['item']
        
        # Extract post text
        item['post_text'] = self._extract_post_text(response)
        
        # Extract engagement metrics
        item['likes'] = self._extract_likes(response)
        item['comments'] = self._extract_comments(response)
        item['shares'] = self._extract_shares(response)
        
        # Sentiment analysis would be done in a pipeline
        item['sentiment_score'] = None
        item['sentiment_polarity'] = None
        
        yield item
    
    def _extract_instagram_followers(self, response):
        """Extract Instagram follower count."""
        # Template implementation
        followers = response.css('meta[name="description"]::attr(content)').re_first(r'(\d+(?:,\d+)*) Followers')
        return followers.replace(',', '') if followers else None
    
    def _extract_twitter_followers(self, response):
        """Extract Twitter/X follower count."""
        # Template implementation
        followers = response.css('a[data-testid="UserDescription"]::text').get()
        return followers
    
    def _calculate_engagement_rate(self, response):
        """Calculate engagement rate from profile data."""
        # Template implementation - would need actual post data
        return None
    
    def _extract_post_urls(self, response):
        """Extract URLs of recent posts."""
        # Template implementation
        return []
    
    def _extract_post_text(self, response):
        """Extract post text/caption."""
        # Template implementation
        return response.css('meta[property="og:title"]::attr(content)').get()
    
    def _extract_likes(self, response):
        """Extract like count."""
        # Template implementation
        return response.css('span::text').re_first(r'(\d+) likes')
    
    def _extract_comments(self, response):
        """Extract comment count."""
        # Template implementation
        return response.css('span::text').re_first(r'(\d+) comments')
    
    def _extract_shares(self, response):
        """Extract share count."""
        # Template implementation
        return None
