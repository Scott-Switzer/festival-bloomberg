import scrapy


class FestivalLineupItem(scrapy.Item):
    """
    Item for storing festival lineup data.
    """
    artist_name = scrapy.Field()
    stage = scrapy.Field()
    timeslot = scrapy.Field()
    date = scrapy.Field()
    year = scrapy.Field()
    day = scrapy.Field()
    weekend = scrapy.Field()
    genre = scrapy.Field()
    festival_name = scrapy.Field()
    scraped_at = scrapy.Field()


class SocialMediaItem(scrapy.Item):
    """
    Item for storing social media sentiment data.
    """
    artist_name = scrapy.Field()
    platform = scrapy.Field()
    followers = scrapy.Field()
    engagement_rate = scrapy.Field()
    sentiment_score = scrapy.Field()
    sentiment_polarity = scrapy.Field()
    post_text = scrapy.Field()
    likes = scrapy.Field()
    comments = scrapy.Field()
    shares = scrapy.Field()
    scraped_at = scrapy.Field()


class ArtistMetricsItem(scrapy.Item):
    """
    Item for storing artist metrics from various platforms.
    """
    artist_name = scrapy.Field()
    spotify_followers = scrapy.Field()
    spotify_popularity = scrapy.Field()
    monthly_listeners = scrapy.Field()
    instagram_followers = scrapy.Field()
    twitter_followers = scrapy.Field()
    youtube_subscribers = scrapy.Field()
    tiktok_followers = scrapy.Field()
    scraped_at = scrapy.Field()
