/**
 * Lightweight TypeScript mirror of Python VADER labels for schema/tests.
 * Heavy lexicon scoring lives in python/festival_bloomberg/vader_sentiment.py.
 */
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
