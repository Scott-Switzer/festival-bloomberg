/**
 * Lightweight TypeScript mirror of Python VADER labels for schema/tests.
 * Heavy lexicon scoring lives in python/festival_bloomberg/vader_sentiment.py.
 */
import { z } from "zod";

export type SentimentLabel = "positive" | "neutral" | "negative";

export type SentimentScore = {
  text: string;
  compound: number;
  pos: number;
  neu: number;
  neg: number;
  label: SentimentLabel;
};

/** Standard VADER compound thresholds. */
export function classifyCompound(compound: number): SentimentLabel {
  if (compound >= 0.05) return "positive";
  if (compound <= -0.05) return "negative";
  return "neutral";
}

const SentimentResultSchema = z.object({
  artist_name: z.string(),
  normalized_artist_name: z.string(),
  sentiment_label: z.enum(["positive", "neutral", "negative"]),
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

const POSITIVE_WORDS = new Set([
  "amazing", "awesome", "great", "excellent", "fantastic", "incredible",
  "love", "loved", "beautiful", "brilliant", "masterpiece", "legendary",
  "stunning", "perfect", "best", "favorite", "outstanding", "phenomenal",
  "spectacular", "magnificent", "superb", "wonderful", "exciting", "energetic",
  "powerful", "emotional", "moving", "inspiring", "talented", "gifted",
  "skilled", "impressive", "remarkable", "exceptional", "hit", "banger",
  "fire", "slaps", "dope", "lit", "goat", "classic", "timeless", "iconic",
  "epic", "mind-blowing", "killer", "tight",
]);

const NEGATIVE_WORDS = new Set([
  "terrible", "awful", "horrible", "bad", "worst", "hate", "hated",
  "disappointing", "boring", "dull", "weak", "poor", "trash", "garbage",
  "overrated", "underwhelming", "lackluster", "mediocre", "average",
  "annoying", "irritating", "painful", "cringe", "embarrassing", "fail",
  "disaster", "catastrophe", "mess", "waste", "pointless", "useless",
  "uninspired", "generic", "formulaic", "repetitive", "lazy", "bland",
  "flat", "dead", "lifeless", "soulless", "empty", "hollow",
]);

export function analyzeSentiment(text: string): {
  sentiment_label: SentimentLabel;
  compound_score: number;
  positive_score: number;
  neutral_score: number;
  negative_score: number;
} {
  const words = text.toLowerCase().match(/\b\w+\b/g) || [];
  const wordCount = words.length;

  if (wordCount === 0) {
    return {
      sentiment_label: "neutral",
      compound_score: 0,
      positive_score: 0,
      neutral_score: 1,
      negative_score: 0,
    };
  }

  let positiveCount = 0;
  let negativeCount = 0;

  for (const word of words) {
    if (POSITIVE_WORDS.has(word)) positiveCount++;
    else if (NEGATIVE_WORDS.has(word)) negativeCount++;
  }

  const positiveScore = positiveCount / wordCount;
  const negativeScore = negativeCount / wordCount;
  const neutralScore = 1 - positiveScore - negativeScore;
  const compoundScore = positiveScore - negativeScore;

  return {
    sentiment_label: classifyCompound(compoundScore),
    compound_score: compoundScore,
    positive_score: Math.max(0, positiveScore),
    neutral_score: Math.max(0, neutralScore),
    negative_score: Math.max(0, negativeScore),
  };
}

export class RSSSentimentScraper {
  async scrapeRSSFeed(feedUrl: string, artistNames: string[]): Promise<SentimentResult[]> {
    try {
      const response = await fetch(feedUrl);
      if (!response.ok) return [];

      const text = await response.text();
      const items = this.parseRSS(text);
      const results: SentimentResult[] = [];

      for (const artistName of artistNames) {
        const mentions = items.filter(
          (item) =>
            item.title.toLowerCase().includes(artistName.toLowerCase()) ||
            item.description.toLowerCase().includes(artistName.toLowerCase()),
        );
        if (mentions.length === 0) continue;

        const allText = mentions.map((m) => `${m.title} ${m.description}`).join(" ");
        const sentiment = analyzeSentiment(allText);
        results.push({
          artist_name: artistName,
          normalized_artist_name: artistName.toLowerCase().trim(),
          ...sentiment,
          source_url: feedUrl,
          source_system: "rss",
          mention_count: mentions.length,
          sample_text: mentions[0]?.title,
          analyzed_at: new Date().toISOString(),
        });
      }

      return results;
    } catch {
      return [];
    }
  }

  private parseRSS(xmlText: string): Array<{ title: string; description: string; link: string }> {
    const items: Array<{ title: string; description: string; link: string }> = [];
    const itemRegex = /<item>([\s\S]*?)<\/item>/g;
    let match: RegExpExecArray | null;

    while ((match = itemRegex.exec(xmlText)) !== null) {
      const itemContent = match[1];
      const titleMatch = /<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?<\/title>/i.exec(itemContent);
      const descMatch = /<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?<\/description>/i.exec(itemContent);
      const linkMatch = /<link>(.*?)<\/link>/i.exec(itemContent);
      if (titleMatch) {
        items.push({
          title: this.stripCDATA(titleMatch[1]),
          description: descMatch ? this.stripCDATA(descMatch[1]) : "",
          link: linkMatch ? linkMatch[1] : "",
        });
      }
    }

    return items;
  }

  private stripCDATA(text: string): string {
    return text.replace("<![CDATA[", "").replace("]]>", "");
  }
}

export class RedditSentimentScraper {
  /**
   * No production Reddit collection is implemented in this repository.
   *
   * This method deliberately returns zero observations. It must NEVER
   * fabricate mentions, engagement, or sentiment scores: missing evidence
   * is NOT_OBSERVED / UNKNOWN, never a neutral score.
   */
  async scrapeReddit(_artistNames: string[], _subreddit = "music"): Promise<SentimentResult[]> {
    return [];
  }
}

export class SentimentAggregator {
  private rssScraper = new RSSSentimentScraper();
  private redditScraper = new RedditSentimentScraper();

  async aggregateSentiment(
    artistNames: string[],
    options: { rssFeeds?: string[]; subreddits?: string[] } = {},
  ): Promise<Map<string, SentimentResult>> {
    /**
     * Returns entries only for artists with at least one real observation.
     * An artist absent from the result map has NOT_OBSERVED evidence and
     * must never be treated as neutral.
     */
    const { rssFeeds = [], subreddits = ["music"] } = options;
    const allResults = new Map<string, SentimentResult[]>();

    for (const artistName of artistNames) {
      allResults.set(artistName, []);
    }

    // Note: Reddit collection is intentionally NOT part of the ensemble
    // until a real, non-fabricating collector exists. The loop below is
    // retained so the aggregation shape is explicit.
    for (const feedUrl of rssFeeds) {
      const results = await this.rssScraper.scrapeRSSFeed(feedUrl, artistNames);
      for (const result of results) {
        const existing = allResults.get(result.artist_name) || [];
        existing.push(result);
        allResults.set(result.artist_name, existing);
      }
    }

    for (const subreddit of subreddits) {
      const results = await this.redditScraper.scrapeReddit(artistNames, subreddit);
      for (const result of results) {
        const existing = allResults.get(result.artist_name) || [];
        existing.push(result);
        allResults.set(result.artist_name, existing);
      }
    }

    const aggregated = new Map<string, SentimentResult>();

    for (const [artistName, results] of allResults.entries()) {
      if (results.length === 0) {
        // No evidence for this artist: do NOT fabricate neutral sentiment.
        // Callers must treat an absent artist as NOT_OBSERVED / UNKNOWN.
        continue;
      }

      const avgCompound =
        results.reduce((sum, r) => sum + r.compound_score, 0) / results.length;
      const avgPositive =
        results.reduce((sum, r) => sum + r.positive_score, 0) / results.length;
      const avgNegative =
        results.reduce((sum, r) => sum + r.negative_score, 0) / results.length;
      const avgNeutral =
        results.reduce((sum, r) => sum + r.neutral_score, 0) / results.length;
      const totalMentions = results.reduce((sum, r) => sum + r.mention_count, 0);

      aggregated.set(artistName, {
        artist_name: artistName,
        normalized_artist_name: artistName.toLowerCase().trim(),
        sentiment_label: classifyCompound(avgCompound),
        compound_score: avgCompound,
        positive_score: avgPositive,
        neutral_score: avgNeutral,
        negative_score: avgNegative,
        source_system: "aggregated",
        mention_count: totalMentions,
        sample_text: results[0]?.sample_text,
        analyzed_at: new Date().toISOString(),
      });
    }

    return aggregated;
  }
}

let defaultAggregator: SentimentAggregator | null = null;

export function getSentimentAggregator(): SentimentAggregator {
  if (!defaultAggregator) {
    defaultAggregator = new SentimentAggregator();
  }
  return defaultAggregator;
}
