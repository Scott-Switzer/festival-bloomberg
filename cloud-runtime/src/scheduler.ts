/**
 * Lifecycle-Aware Scheduling Policy.
 *
 * Observation cadence varies by time-to-show, not uniform polling.
 * These thresholds are EXPLICIT CONFIGURABLE POLICY with provenance/version.
 *
 * Policy version: 1.0.0
 * Created: 2026-08-26
 */

/** Versioned policy — not immutable business truth */
export const POLICY_VERSION = "1.0.0";

/**
 * Time-to-show thresholds and corresponding collection frequencies.
 * All values in hours for computation; human-readable labels for display.
 */
export interface CadenceRule {
  min_days_to_show: number;
  max_days_to_show: number;
  collections_per_day: number;
  label: string;
}

/** Default cadence policy */
export const DEFAULT_CADENCE_POLICY: CadenceRule[] = [
  { min_days_to_show: 120, max_days_to_show: 9999, collections_per_day: 1 / 7, label: "weekly" },
  { min_days_to_show: 60, max_days_to_show: 120, collections_per_day: 2 / 7, label: "2x/week" },
  { min_days_to_show: 30, max_days_to_show: 60, collections_per_day: 1, label: "daily" },
  { min_days_to_show: 14, max_days_to_show: 30, collections_per_day: 1, label: "daily" },
  { min_days_to_show: 7, max_days_to_show: 14, collections_per_day: 2, label: "2x/day" },
  { min_days_to_show: 3, max_days_to_show: 7, collections_per_day: 3, label: "3x/day" },
  { min_days_to_show: 1, max_days_to_show: 3, collections_per_day: 4, label: "4x/day" },
  { min_days_to_show: 0, max_days_to_show: 1, collections_per_day: 6, label: "4-6x/day (show day)" },
];

/** Trigger thresholds for event-driven captures */
export interface EventDrivenTrigger {
  trigger_type:
    | "PRICE_SHOCK"
    | "LISTING_COUNT_SHOCK"
    | "PRESALE_DISCOVERED"
    | "ONSALE_DISCOVERED"
    | "CANCELLATION"
    | "POSTPONEMENT"
    | "RESCHEDULE"
    | "VENUE_CHANGE"
    | "NEW_MAPPING"
    | "PROVIDER_STATUS_CHANGE"
    | "MAJOR_ATTENTION_SHOCK";
  /** Threshold that must be exceeded to trigger */
  threshold: number;
  /** Cooldown period in seconds after trigger before re-triggering */
  cooldown_seconds: number;
}

export const DEFAULT_EVENT_DRIVEN_TRIGGERS: EventDrivenTrigger[] = [
  { trigger_type: "PRICE_SHOCK", threshold: 0.15, cooldown_seconds: 3600 },       // 15% price change
  { trigger_type: "LISTING_COUNT_SHOCK", threshold: 0.25, cooldown_seconds: 3600 }, // 25% listing count change
  { trigger_type: "PRESALE_DISCOVERED", threshold: 0, cooldown_seconds: 86400 },
  { trigger_type: "ONSALE_DISCOVERED", threshold: 0, cooldown_seconds: 86400 },
  { trigger_type: "CANCELLATION", threshold: 0, cooldown_seconds: 0 },             // always re-trigger
  { trigger_type: "POSTPONEMENT", threshold: 0, cooldown_seconds: 0 },
  { trigger_type: "NEW_MAPPING", threshold: 0, cooldown_seconds: 0 },
];

/**
 * Determine whether an event should be observed right now,
 * given its time-to-show and last observation time.
 */
export function shouldObserveNow(
  days_to_show: number,
  last_observed_hours_ago: number,
  cadence_policy: CadenceRule[] = DEFAULT_CADENCE_POLICY
): boolean {
  // Post-show: stop routine secondary-market acquisition
  if (days_to_show < 0) return false;

  const rule = cadence_policy.find(
    (r) => days_to_show >= r.min_days_to_show && days_to_show < r.max_days_to_show
  );
  if (!rule) return false;

  const hours_between_observations = 24 / rule.collections_per_day;
  return last_observed_hours_ago >= hours_between_observations;
}

/**
 * Calculate the next scheduled observation time.
 */
export function nextObservationTime(
  days_to_show: number,
  cadence_policy: CadenceRule[] = DEFAULT_CADENCE_POLICY
): { hours_until: number; rule: CadenceRule } | null {
  if (days_to_show < 0) return null;

  const rule = cadence_policy.find(
    (r) => days_to_show >= r.min_days_to_show && days_to_show < r.max_days_to_show
  );
  if (!rule) return null;

  return {
    hours_until: 24 / rule.collections_per_day,
    rule,
  };
}
