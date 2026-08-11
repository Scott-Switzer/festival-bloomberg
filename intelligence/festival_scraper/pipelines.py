import json
import os
from datetime import datetime


class FestivalScraperPipeline:
    """
    Pipeline for processing scraped festival data.
    """
    
    def __init__(self):
        self.output_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'warehouse', 'scraped')
        os.makedirs(self.output_dir, exist_ok=True)
        self.timestamp = datetime.now().isoformat()
    
    def process_item(self, item, spider):
        """
        Process and save scraped item to JSON file.
        """
        # Convert Scrapy Item to dict
        item_dict = dict(item)
        
        # Add timestamp if not present
        if 'scraped_at' not in item_dict or not item_dict['scraped_at']:
            item_dict['scraped_at'] = self.timestamp
        
        # Determine output file based on spider name
        spider_name = spider.name
        output_file = os.path.join(self.output_dir, f"{spider_name}_{self.timestamp}.jsonl")
        
        # Append to JSONL file
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(item_dict) + '\n')
        
        return item


class SentimentAnalysisPipeline:
    """
    Pipeline for performing sentiment analysis on scraped social media data.
    """
    
    def __init__(self):
        try:
            from textblob import TextBlob
            self.TextBlob = TextBlob
            self.enabled = True
        except ImportError:
            self.enabled = False
            spider.logger.warning("TextBlob not installed. Sentiment analysis disabled.")
    
    def process_item(self, item, spider):
        """
        Perform sentiment analysis on post text.
        """
        if not self.enabled or not item.get('post_text'):
            return item
        
        try:
            text = item['post_text']
            blob = self.TextBlob(text)
            
            # Get sentiment polarity (-1 to 1)
            sentiment_polarity = blob.sentiment.polarity
            
            # Get sentiment subjectivity (0 to 1)
            sentiment_subjectivity = blob.sentiment.subjectivity
            
            # Classify sentiment
            if sentiment_polarity > 0.1:
                sentiment = 'positive'
            elif sentiment_polarity < -0.1:
                sentiment = 'negative'
            else:
                sentiment = 'neutral'
            
            item['sentiment_score'] = sentiment_polarity
            item['sentiment_polarity'] = sentiment
            item['sentiment_subjectivity'] = sentiment_subjectivity
            
        except Exception as e:
            spider.logger.error(f"Sentiment analysis failed: {e}")
        
        return item


class DeduplicationPipeline:
    """
    Pipeline for deduplicating scraped items.
    """
    
    def __init__(self):
        self.seen_items = set()
    
    def process_item(self, item, spider):
        """
        Deduplicate items based on a unique key.
        """
        # Create a unique key based on item type
        if 'artist_name' in item and 'festival_name' in item:
            # Festival lineup item
            unique_key = f"{item['artist_name']}_{item['festival_name']}_{item.get('year', '')}"
        elif 'artist_name' in item and 'platform' in item:
            # Social media item
            unique_key = f"{item['artist_name']}_{item['platform']}_{item.get('scraped_at', '')}"
        else:
            # Generic item
            unique_key = str(dict(item))
        
        if unique_key in self.seen_items:
            spider.logger.debug(f"Duplicate item skipped: {unique_key}")
            return None
        
        self.seen_items.add(unique_key)
        return item


class DataValidationPipeline:
    """
    Pipeline for validating scraped data.
    """
    
    def process_item(self, item, spider):
        """
        Validate required fields and data quality.
        """
        # Define required fields for each item type
        required_fields = {
            'artist_name': ['artist_name'],
            'festival_name': ['artist_name', 'festival_name'],
            'social_media': ['artist_name', 'platform'],
        }
        
        # Determine item type and validate
        if 'festival_name' in item:
            item_type = 'festival_name'
        elif 'platform' in item:
            item_type = 'social_media'
        else:
            item_type = 'artist_name'
        
        # Check required fields
        for field in required_fields.get(item_type, []):
            if not item.get(field):
                spider.logger.warning(f"Missing required field '{field}' in item: {item}")
                return None
        
        # Validate data types
        if item.get('followers'):
            try:
                item['followers'] = int(str(item['followers']).replace(',', '').replace('K', '000').replace('M', '000000'))
            except (ValueError, AttributeError):
                spider.logger.warning(f"Invalid follower count: {item.get('followers')}")
                item['followers'] = None
        
        if item.get('year'):
            try:
                item['year'] = int(item['year'])
            except (ValueError, AttributeError):
                spider.logger.warning(f"Invalid year: {item.get('year')}")
        
        return item
