/**
 * DecisionProvider — provider-agnostic interface for typed probabilistic decisions.
 *
 * Implementations:
 *  - JevGatewayClient: POSTs to the Cloudflare jev-gateway Worker, which forwards to
 *    api.typesafe.ai/v1/systemone. Used once the TypeSafe key is live.
 *  - SystemOneAdapterClient: same interface backed by a regular LLM via TypeSafe's
 *    System One Adapter. Used NOW for development and benchmarking — no key needed.
 *
 * Callers (scraper, terminal, warehouse jobs) depend only on this interface.
 * Swapping providers is a constructor argument, not a rewrite.
 */

export type QuestionType = "choice" | "score" | "noul";

export interface Question {
  type: QuestionType;
  instructions: string;
  criteria?: Record<string, string> | string[];
}

export interface ChoiceAnswer {
  type: "choice";
  choice: string;
  probabilities: Record<string, number>;
  confidence: number;
}

export interface ScoreAnswer {
  type: "score";
  score: number;
  legend: Record<string, string>;
  confidence: number;
}

export interface NoulAnswer {
  type: "noul";
  noul: number; // 0..1
}

export type Answer = ChoiceAnswer | ScoreAnswer | NoulAnswer;

export interface DecideInput {
  /** Versioned question set, e.g. "festival-extraction@v1", or inline questions. */
  questionSet?: string;
  questions?: Record<string, Question>;
  /** Unstructured state the questions are evaluated against. */
  state: string;
  /** Cache TTL hint in seconds. 0 = no cache. */
  cacheTtlSeconds?: number;
}

export interface DecideOutput {
  answers: Record<string, Answer>;
  usage: { inputTokens: number; outputTokens: number };
  gateway: { cache: "hit" | "miss"; latencyMs: number; questionSet?: string };
}

export interface DecisionProvider {
  decide(input: DecideInput): Promise<DecideOutput>;
}

/**
 * Talks to the jev-gateway Worker. The TypeSafe API key never leaves Cloudflare.
 */
export class JevGatewayClient implements DecisionProvider {
  constructor(
    private gatewayUrl: string,
    private service: string,
    private serviceKey: string,
  ) {}

  async decide(input: DecideInput): Promise<DecideOutput> {
    const res = await fetch(`${this.gatewayUrl}/v1/decide`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-service": this.service,
        "x-service-key": this.serviceKey,
      },
      body: JSON.stringify({
        service: this.service,
        service_key: this.serviceKey,
        question_set: input.questionSet,
        questions: input.questions,
        state: input.state,
        cache_ttl_seconds: input.cacheTtlSeconds ?? 0,
      }),
    });
    if (!res.ok) {
      throw new Error(`jev-gateway ${res.status}: ${await res.text()}`);
    }
    const data = await res.json();
    return {
      answers: data.answers,
      usage: {
        inputTokens: data.usage.input_tokens,
        outputTokens: data.usage.output_tokens,
      },
      gateway: data.gateway,
    };
  }
}

/**
 * Confidence-gated routing helper. Implements the escalation ladder so every caller
 * doesn't reinvent it: high confidence → act, medium → flag, low → fallback.
 */
export async function decideWithEscalation<T>(
  provider: DecisionProvider,
  input: DecideInput,
  handlers: {
    onConfident: (answers: Record<string, Answer>) => Promise<T>;
    onUncertain: (answers: Record<string, Answer>) => Promise<T>;
    onLowConfidence: (answers: Record<string, Answer>) => Promise<T>;
    /** Minimum confidence across all answers to count as "confident". */
    confidentThreshold?: number;
    uncertainThreshold?: number;
  },
): Promise<T> {
  const { answers } = await provider.decide(input);
  const minConfidence = Math.min(
    ...Object.values(answers).map((a) =>
      a.type === "noul" ? Math.abs(a.noul - 0.5) * 2 : a.confidence,
    ),
  );
  const confidentAt = handlers.confidentThreshold ?? 0.9;
  const uncertainAt = handlers.uncertainThreshold ?? 0.7;
  if (minConfidence >= confidentAt) return handlers.onConfident(answers);
  if (minConfidence >= uncertainAt) return handlers.onUncertain(answers);
  return handlers.onLowConfidence(answers);
}
