import scrapy
from festival_scraper.items import FestivalLineupItem


class FestivalLineupSpider(scrapy.Spider):
    """
    Spider for scraping festival lineup data from various festival websites.
    This spider can be configured to target different festival websites.
    """
    name = "festival_lineup"
    allowed_domains = ["festivalviewer.com", "glastonburyfestivals.co.uk"]
    start_urls = ["https://festivalviewer.com/tomorrowland/lineup/home"]
    
    custom_settings = {
        'USER_AGENT': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'ROBOTSTXT_OBEY': True,
        'DOWNLOAD_DELAY': 2,
        'CONCURRENT_REQUESTS': 16,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 8,
    }
    
    def parse(self, response):
        """
        Parse festival lineup page and extract artist information.
        """
        # Extract festival name from URL or page title
        festival_name = self.extract_festival_name(response)
        
        # Extract artist data from the lineup table
        for artist_row in response.css('table tbody tr'):
            item = FestivalLineupItem()
            
            # Extract artist name
            item['artist_name'] = artist_row.css('td:nth-child(1)::text').get()
            
            # Extract stage
            item['stage'] = artist_row.css('td:nth-child(2)::text').get()
            
            # Extract timeslot
            item['timeslot'] = artist_row.css('td:nth-child(4)::text').get()
            
            # Extract date
            item['date'] = artist_row.css('td:nth-child(5)::text').get()
            
            # Extract year
            item['year'] = artist_row.css('td:nth-child(6)::text').get()
            
            # Extract day
            item['day'] = artist_row.css('td:nth-child(7)::text').get()
            
            # Extract genre
            item['genre'] = artist_row.css('td:nth-child(8)::text').get()
            
            # Add festival metadata
            item['festival_name'] = festival_name
            item['scraped_at'] = scrapy.utils.project.get_project_settings().get('SCRAPE_TIMESTAMP')
            
            if item['artist_name']:
                yield item
    
    def extract_festival_name(self, response):
        """
        Extract festival name from the response.
        """
        # Try to get from title
        title = response.css('title::text').get()
        if title:
            return title.split('-')[0].strip()
        
        # Try to get from URL
        url = response.url
        if 'tomorrowland' in url:
            return 'Tomorrowland'
        elif 'glastonbury' in url:
            return 'Glastonbury'
        
        return 'Unknown Festival'


class GlastonburySpider(scrapy.Spider):
    """
    Spider specifically for Glastonbury Festival lineup data.
    """
    name = "glastonbury"
    allowed_domains = ["glastonburyfestivals.co.uk"]
    start_urls = ["https://www.glastonburyfestivals.co.uk/line-up/"]
    
    custom_settings = {
        'USER_AGENT': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'ROBOTSTXT_OBEY': True,
        'DOWNLOAD_DELAY': 3,
    }
    
    def parse(self, response):
        """
        Parse Glastonbury lineup page.
        """
        for artist in response.css('.lineup-item'):
            item = FestivalLineupItem()
            item['artist_name'] = artist.css('.artist-name::text').get()
            item['stage'] = artist.css('.stage-name::text').get()
            item['festival_name'] = 'Glastonbury'
            item['scraped_at'] = scrapy.utils.project.get_project_settings().get('SCRAPE_TIMESTAMP')
            
            if item['artist_name']:
                yield item
