/**
 * RSS/Reddit sentiment scraper module.
 * Crawls public threads for artist names and scores positive/negative sentiment.
 */

import { z } from 'zod';

// ===========================================================================
// Sentiment Analysis Types
// ===========================================================================

const SentimentResultSchema = z.object({
  artist_name: z.string(),
  normalized_artist_name: z.string(),
  sentiment_label: z.enum(['positive', 'neutral', 'negative']),
  compound_score: z.number().min(-1).max(1),
  positive_score: z.number().min(0).max(1),
  neutral_score: z.number().min(0).max(1),
  negative_score: z.number().min(0).max(1),
  source_url: z.string().url().optional(),
  source_system: z.string(),
  mention_count: z.number().int().nonnegative(),
  sample_text: z.string().optional(),
  analyzed_at: z.string(),
});

export type SentimentResult = z.infer<typeof SentimentResultSchema>;

// ===========================================================================
// Simple Sentiment Analyzer (VADER-inspired)
// ===========================================================================

/**
 * Simple sentiment lexicon for music-related content.
 * In production, this would use a proper NLP library or API.
 */
const POSITIVE_WORDS = new Set([
  'amazing', 'awesome', 'great', 'excellent', 'fantastic', 'incredible',
  'love', 'loved', 'beautiful', 'brilliant', 'masterpiece', 'legendary',
  'stunning', 'perfect', 'best', 'favorite', 'incredible', 'outstanding',
  'phenomenal', 'spectacular', 'magnificent', 'superb', 'wonderful',
  'exciting', 'energetic', 'powerful', 'emotional', 'moving', 'inspiring',
  'talented', 'gifted', 'skilled', 'impressive', 'remarkable', 'exceptional',
  'hit', 'banger', 'fire', 'slaps', 'dope', 'lit', 'goat', 'classic',
  'timeless', 'iconic', 'epic', 'mind-blowing', 'killer', 'tight',
]);

const NEGATIVE_WORDS = new Set([
  'terrible', 'awful', 'horrible', 'bad', 'worst', 'hate', 'hated',
  'disappointing', 'boring', 'dull', 'weak', 'poor', 'trash', 'garbage',
  'overrated', 'underwhelming', 'lackluster', 'mediocre', 'average',
  'annoying', 'irritating', 'painful', 'cringe', 'embarrassing', 'fail',
  'disaster', 'catastrophe', 'mess', 'waste', 'pointless', 'useless',
  'uninspired', 'generic', 'formulaic', 'repetitive', 'lazy', 'bland',
  'flat', 'dead', 'lifeless', 'soulless', 'empty', 'hollow',
]);

/**
 * Analyze sentiment of text using simple lexicon-based approach.
 */
export function analyzeSentiment(text: string): {
  sentiment_label: 'positive' | 'neutral' | 'negative';
  compound_score: number;
  positive_score: number;
  neutral_score: number;
  negative_score: number;
} {
  const words = text.toLowerCase().match(/\b\w+\b/g) || [];
  const wordCount = words.length;
  
  if (wordCount === 0) {
    return {
      sentiment_label: 'neutral',
      compound_score: 0,
      positive_score: 0,
      neutral_score: 1,
      negative_score: 0,
    };
  }

  let positiveCount = 0;
  let negativeCount = 0;

  for (const word of words) {
    if (POSITIVE_WORDS.has(word)) {
      positiveCount++;
    } else if (NEGATIVE_WORDS.has(word)) {
      negativeCount++;
    }
  }

  const positiveScore = positiveCount / wordCount;
  const negativeScore = negativeCount / wordCount;
  const neutralScore = 1 - positiveScore - negativeScore;

  // Calculate compound score (-1 to 1)
  const compoundScore = positiveScore - negativeScore;

  let sentimentLabel: 'positive' | 'neutral' | 'negative';
  if (compoundScore > 0.05) {
    sentimentLabel = 'positive';
  } else if (compoundScore < -0.05) {
    sentimentLabel = 'negative';
  } else {
    sentimentLabel = 'neutral';
  }

  return {
    sentiment_label: sentimentLabel,
    compound_score: compoundScore,
    positive_score: Math.max(0, positiveScore),
    neutral_score: Math.max(0, neutralScore),
    negative_score: Math.max(0, negativeScore),
  };
}

// ===========================================================================
// RSS Feed Scraper
// ===========================================================================

export class RSSSentimentScraper {
  /**
   * Fetch and parse RSS feed for artist mentions.
   */
  async scrapeRSSFeed(feedUrl: string, artistNames: string[]): Promise<SentimentResult[]> {
    try {
      const response = await fetch(feedUrl);
      if (!response.ok) {
        console.error(`Failed to fetch RSS feed: ${response.status}`);
        return [];
      }

      const text = await response.text();
      const items = this.parseRSS(text);
      
      const results: SentimentResult[] = [];
      
      for (const artistName of artistNames) {
        const mentions = items.filter(item => 
          item.title.toLowerCase().includes(artistName.toLowerCase()) ||
          item.description.toLowerCase().includes(artistName.toLowerCase())
        );

        if (mentions.length === 0) continue;

        const allText = mentions.map(m => `${m.title} ${m.description}`).join(' ');
        const sentiment = analyzeSentiment(allText);

        results.push({
          artist_name: artistName,
          normalized_artist_name: artistName.toLowerCase().trim(),
          ...sentiment,
          source_url: feedUrl,
          source_system: 'rss',
          mention_count: mentions.length,
          sample_text: mentions[0]?.title,
          analyzed_at: new Date().toISOString(),
        });
      }

      return results;
    } catch (error) {
      console.error(`Error scraping RSS feed ${feedUrl}:`, error);
      return [];
    }
  }

  /**
   * Simple RSS parser (XML format).
   */
  private parseRSS(xmlText: string): Array<{ title: string; description: string; link: string }> {
    const items: Array<{ title: string; description: string; link: string }> = [];
    
    // Simple regex-based parsing (in production, use proper XML parser)
    const itemRegex = /<item>([\s\S]*?)<\/item>/g;
    let match;
    
    while ((match = itemRegex.exec(xmlText)) !== null) {
      const itemContent = match[1];
      
      const titleMatch = /<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?<\/title>/i.exec(itemContent);
      const descMatch = /<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?<\/description>/i.exec(itemContent);
      const linkMatch = /<link>(.*?)<\/link>/i.exec(itemContent);
      
      if (titleMatch) {
        items.push({
          title: this.stripCDATA(titleMatch[1]),
          description: descMatch ? this.stripCDATA(descMatch[1]) : '',
          link: linkMatch ? linkMatch[1] : '',
        });
      }
    }
    
    return items;
  }

  private stripCDATA(text: string): string {
    return text.replace('<![CDATA[', '').replace(']]>', '');
  }
}

// ===========================================================================
// Reddit Scraper (Simulated)
// ===========================================================================

export class RedditSentimentScraper {
  /**
   * Scrape Reddit for artist mentions (simulated - requires API in production).
   */
  async scrapeReddit(artistNames: string[], subreddit: string = 'music'): Promise<SentimentResult[]> {
    // In production, this would use the Reddit API or PRAW
    // For now, we'll simulate the structure
    
    const results: SentimentResult[] = [];
    
    for (const artistName of artistNames) {
      // Simulated scraping - in production, fetch actual Reddit data
      const mockMentions = this.generateMockMentions(artistName, subreddit);
      
      if (mockMentions.length === 0) continue;

      const allText = mockMentions.join(' ');
      const sentiment = analyzeSentiment(allText);

      results.push({
        artist_name: artistName,
        normalized_artist_name: artistName.toLowerCase().trim(),
        ...sentiment,
        source_url: `https://reddit.com/r/${subreddit}`,
        source_system: 'reddit',
        mention_count: mockMentions.length,
        sample_text: mockMentions[0],
        analyzed_at: new Date().toISOString(),
      });
    }

    return results;
  }

  /**
   * Generate mock mentions for testing (replace with actual API calls).
   */
  private generateMockMentions(artistName: string, _subreddit: string): string[] {
    // This is a placeholder - in production, fetch actual Reddit posts/comments
    const mockComments = [
      `${artistName} is absolutely amazing live!`,
      `I think ${artistName}'s new album is disappointing.`,
      `${artistName} has such a unique sound, love it.`,
      `Not a fan of ${artistName}'s recent work.`,
      `${artistName} is a legend in the making.`,
    ];
    
    // Return random subset to simulate real data
    return mockComments.slice(0, Math.floor(Math.random() * mockComments.length) + 1);
  }
}

// ===========================================================================
// Aggregated Sentiment Service
// ===========================================================================

export class SentimentAggregator {
  private rssScraper: RSSSentimentScraper;
  private redditScraper: RedditSentimentScraper;

  constructor() {
    this.rssScraper = new RSSSentimentScraper();
    this.redditScraper = new RedditSentimentScraper();
  }

  /**
   * Aggregate sentiment from multiple sources for a list of artists.
   */
  async aggregateSentiment(
    artistNames: string[],
    options: {
      rssFeeds?: string[];
      subreddits?: string[];
    } = {}
  ): Promise<Map<string, SentimentResult>> {
    const { rssFeeds = [], subreddits = ['music'] } = options;
    
    const allResults = new Map<string, SentimentResult[]>();

    // Initialize result arrays for each artist
    for (const artistName of artistNames) {
      allResults.set(artistName, []);
    }

    // Scrape RSS feeds
    for (const feedUrl of rssFeeds) {
      const results = await this.rssScraper.scrapeRSSFeed(feedUrl, artistNames);
      for (const result of results) {
        const existing = allResults.get(result.artist_name) || [];
        existing.push(result);
        allResults.set(result.artist_name, existing);
      }
    }

    // Scrape Reddit
    for (const subreddit of subreddits) {
      const results = await this.redditScraper.scrapeReddit(artistNames, subreddit);
      for (const result of results) {
        const existing = allResults.get(result.artist_name) || [];
        existing.push(result);
        allResults.set(result.artist_name, existing);
      }
    }

    // Aggregate results per artist
    const aggregated = new Map<string, SentimentResult>();

    for (const [artistName, results] of allResults.entries()) {
      if (results.length === 0) {
        // No data available
        aggregated.set(artistName, {
          artist_name: artistName,
          normalized_artist_name: artistName.toLowerCase().trim(),
          sentiment_label: 'neutral',
          compound_score: 0,
          positive_score: 0,
          neutral_score: 1,
          negative_score: 0,
          source_system: 'aggregated',
          mention_count: 0,
          analyzed_at: new Date().toISOString(),
        });
        continue;
      }

      // Average the scores
      const avgCompound = results.reduce((sum, r) => sum + r.compound_score, 0) / results.length;
      const avgPositive = results.reduce((sum, r) => sum + r.positive_score, 0) / results.length;
      const avgNegative = results.reduce((sum, r) => sum + r.negative_score, 0) / results.length;
      const avgNeutral = results.reduce((sum, r) => sum + r.neutral_score, 0) / results.length;
      const totalMentions = results.reduce((sum, r) => sum + r.mention_count, 0);

      let sentimentLabel: 'positive' | 'neutral' | 'negative';
      if (avgCompound > 0.05) {
        sentimentLabel = 'positive';
      } else if (avgCompound < -0.05) {
        sentimentLabel = 'negative';
      } else {
        sentimentLabel = 'neutral';
      }

      aggregated.set(artistName, {
        artist_name: artistName,
        normalized_artist_name: artistName.toLowerCase().trim(),
        sentiment_label: sentimentLabel,
        compound_score: avgCompound,
        positive_score: avgPositive,
        neutral_score: avgNeutral,
        negative_score: avgNegative,
        source_system: 'aggregated',
        mention_count: totalMentions,
        sample_text: results[0]?.sample_text,
        analyzed_at: new Date().toISOString(),
      });
    }

    return aggregated;
  }
}

// ===========================================================================
// Default Instance
// ===========================================================================

let defaultAggregator: SentimentAggregator | null = null;

export function getSentimentAggregator(): SentimentAggregator {
  if (!defaultAggregator) {
    defaultAggregator = new SentimentAggregator();
  }
  return defaultAggregator;
}
