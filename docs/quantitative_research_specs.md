# Quantitative Research Module Specifications

Status: design blueprint
Scope: five research modules for booking, underwriting, and sponsor decisions. This document specifies reproducible inputs, transformations, mathematical definitions, schemas, and acceptance tests. It is not a claim that any external feed is complete or licensed for production use.

## 0. Common contract

Every source adapter must emit immutable raw snapshots and normalized observations. A snapshot is identified by `source`, `request_uri`, `retrieved_at_utc`, `http_status`, `content_hash`, `schema_version`, and `license_note`. Do not silently replace missing values with zero. Preserve `null`, a `quality_flags[]` array, and the reason for exclusion.

Canonical identifiers:

- `artist_id`, `agency_id`, `event_id`, `venue_id`, `city_id`, `country_code` (ISO 3166-1 alpha-2), `currency` (ISO 4217), and `brand_id`.
- Timestamps are ISO-8601 UTC; event local date/time and IANA timezone are retained separately.
- Money is integer minor units plus currency, never a floating-point display amount.
- Distances are kilometers; temperature is Celsius; precipitation is millimeters internally (1 inch = 25.4 mm).

Minimum lineage record:

```json
{
  "observation_id": "uuid",
  "entity_id": "string",
  "as_of_utc": "timestamp",
  "source": "string",
  "source_uri": "string",
  "retrieved_at_utc": "timestamp",
  "raw_hash": "sha256",
  "value": {},
  "quality_flags": ["string"]
}
```

Common verification gates:

1. schema/type validation and uniqueness of natural keys;
2. freshness, coverage, and duplicate-rate checks;
3. deterministic re-run against a frozen fixture (same input hash gives same output hash);
4. unit/invariant tests for every equation;
5. out-of-sample validation with time-based splits and no future-data leakage;
6. model calibration and sensitivity report before a risk score is exposed.

## 1. Agency Concentration & Package-Deal Risk

### 1.1 Objective and sources

Measure how much lineup attention is concentrated among agencies and whether a package deal creates correlated availability, negotiation, or cancellation exposure. Candidate public roster sources are CAA, WME, UTA, and Wasserman artist/entertainment roster pages, plus the festival lineup and schedule snapshot. Roster pages are point-in-time evidence, not a definitive representation agreement; store the page date and confidence.

Do not infer representation from a search result, social bio, or a shared bill. Agency assignment is valid only when a roster page, an authorized first-party feed, or a manually reviewed record supports it.

### 1.2 Input schema

```json
{
  "lineup_event_id": "string",
  "artist_id": "string",
  "billing_weight": "number >= 0",
  "stage_minutes": "number >= 0",
  "estimated_attention": "number >= 0",
  "agency_id": "CAA|WME|UTA|Wasserman|unverified|other",
  "agency_evidence": {"uri":"string", "captured_at":"timestamp", "confidence":"high|medium|low"},
  "fee_minor": "integer|null",
  "cancellation_correlation_group": "string|null"
}
```

`estimated_attention` must be defined once per analysis: ticket-demand share, stage-minute share, or an explicitly weighted combination. The default is `billing_weight * log1p(stage_minutes)`; weights and normalization are configuration, not hidden constants.

### 1.3 Definitions

For agency `a`, lineup `L`, and nonnegative attention `w_i`:

`W = sum_i w_i`; `s_a = sum_{i:agency_i=a} w_i / W`.

Agency share of lineup attention is `s_a`. The Herfindahl-Hirschman Index is:

`HHI = sum_a (100*s_a)^2`, ranging from 0 to 10,000. The normalized form is `HHI* = (HHI - 10,000/K)/(10,000 - 10,000/K)` for `K > 1`; it is 0 for equal shares and 1 for a monopoly. Report both, together with the number of agencies and the unclassified share.

Package-deal exposure for a set `P` is:

`PDE(P) = sum_{i in P} w_i / W`.

If artist cancellation indicators are `Y_i`, use pairwise empirical correlation `rho_ij` only with adequate historical observations. A portfolio correlation proxy is:

`CR = (w' R w) / (sum_i w_i)^2`, where `R_ii=1`, `R_ij=rho_ij`, and `R` is projected to the nearest positive-semidefinite matrix if needed. Never treat CR as a probability.

A simple agency risk index is a separately calibrated score:

`ARI = alpha*HHI* + beta*max_a(s_a) + gamma*PDE(P*) + delta*CR`, with nonnegative coefficients summing to 1 and `P*` the largest plausible package group. Store coefficients, calibration data, and confidence intervals.

### 1.4 Validation plan

- Synthetic shares `[1,0,0]`, `[.5,.5,0]`, and `[.25,.25,.25,.25]` must produce HHI 10,000, 5,000, and 2,500 respectively.
- Duplicating an artist must fail uniqueness or leave the result unchanged only after explicit deduplication by `artist_id`.
- Moving one artist between agencies must change only the affected shares and HHI.
- Missing agency evidence must increase `unclassified_share` and never be assigned to an agency.
- Recompute scores after removing the top agency; report delta-HHI and delta-ARI.
- Backtest historical lineups with rolling snapshots; compare predicted package-level disruption with observed artist withdrawal/availability outcomes using Brier score, calibration slope, and precision/recall at the underwriting threshold.

## 2. Tour-Routing Efficiency & TSP Booking Margin

### 2.1 Objective and sources

Use Bandsintown and Songkick schedules as discovery inputs, subject to their terms and rate limits. Normalize announced gigs into a route graph. The module estimates avoidable travel cost and the booking margin created by inserting or ordering shows; it is not a promise that the artist can accept a date.

### 2.2 Schedule and venue schema

```json
{
  "artist_id":"string",
  "event_id":"string",
  "venue_id":"string|null",
  "venue_name":"string",
  "city":"string",
  "country_code":"string",
  "latitude":"number|null",
  "longitude":"number|null",
  "start_local":"timestamp",
  "timezone":"IANA timezone",
  "source":"bandsintown|songkick|manual",
  "source_event_id":"string",
  "status":"announced|cancelled|postponed|unknown",
  "guarantee_minor":"integer|null",
  "routing_cost_minor":"integer|null"
}
```

Deduplicate by artist plus normalized venue/date, retaining all source IDs. Geocode only with a logged resolver; reject impossible coordinates and retain the unresolved record.

### 2.3 Distance, route, and margin

For coordinates `(phi_1, lambda_1)` and `(phi_2, lambda_2)` in radians, Haversine distance is:

`a = sin^2((phi_2-phi_1)/2) + cos(phi_1)cos(phi_2)sin^2((lambda_2-lambda_1)/2)`

`d = 2R*atan2(sqrt(a), sqrt(1-a))`, with `R = 6371.0088 km`. Clamp `a` to `[0,1]` for floating-point safety.

For consecutive gigs ordered by local start date, `D_route = sum_j d(j,j+1)`. A candidate gig `c` inserted between predecessor `p` and successor `q` has incremental distance:

`DeltaD = d(p,c) + d(c,q) - d(p,q)`.

Use an explicit cost function, for example:

`C(D, n, overnight) = fixed_transport + variable_cost_per_km*D + lodging_per_night*overnights + per_diem_per_person*headcount*days + deadhead_penalty`.

Booking margin is:

`M = guarantee + expected_local_revenue + sponsor_allocation - incremental_cost - incremental_risk_reserve - opportunity_cost`.

A route optimizer must specify whether endpoints are fixed, whether dates are hard time windows, and whether return-to-origin is required. Exact TSP is factorial; production may use Held-Karp for small N and a documented heuristic (2-opt/3-opt or constrained OR solver) for larger N. Always report a lower bound and optimality gap when exact optimum is unavailable.

### 2.4 LAX-SFO fixture and tests

Use a frozen fixture containing LAX `(33.9416,-118.4085)` and SFO `(37.6213,-122.3790)`, with coordinates explicitly labeled as airport reference points. The expected great-circle distance is approximately 543 km; acceptance is ±2 km for the specified coordinates and radius. The reverse route must be equal within 1 meter. Identical points must return 0 within 1e-9 km. A candidate inserted between two identical endpoints must have `DeltaD >= 0`.

Additional verification:

- timezone conversion must not reorder gigs around midnight or daylight-saving transitions;
- cancelled events are excluded from the active route but remain in lineage;
- duplicate source events cannot double-count guarantee or distance;
- compare optimized distance and margin to a deterministic chronological baseline;
- property test triangle inequality within numerical tolerance;
- validate against hand-calculated three-city routes and report solver optimality gap.

## 3. FX Volatility & Cross-Border Booking Arbitrage

### 3.1 Objective and source

Use the keyless European Central Bank currency feed for reference rates. The feed is an indicative reference series, not a tradable quote. Persist the ECB publication date, base currency, rate timestamp/date, and retrieval metadata. Do not use ECB data for intraday execution or imply guaranteed conversion.

### 3.2 Exposure schema

```json
{
  "event_id":"string",
  "promoter_home_currency":"USD",
  "settlement_currency":"EUR",
  "settlement_amount_minor": "integer",
  "expected_settlement_date":"date",
  "cost_currency":"string",
  "cost_amount_minor":"integer",
  "fx_source_date":"date",
  "rate_base_to_quote":"number > 0",
  "hedge_status":"unhedged|hedged|unknown"
}
```

Define `r_{A/B}` as units of B per one unit of A. Conversion from settlement currency S to home currency H is `S_amount * r_{S/H}`. If the feed provides the inverse, use `r_{S/H}=1/r_{H/S}` and record the inversion.

### 3.3 Volatility and arbitrage definitions

Log return for rate `r_t` is `g_t = ln(r_t/r_{t-1})`. For window `N`, sample volatility is:

`sigma_N = sqrt((1/(N-1))*sum_{t=1}^N (g_t - g_bar)^2)`.

Annualize only with an explicit observation-frequency factor `A`: `sigma_ann = sigma_N*sqrt(A)`. For sparse ECB business-day data, count actual observations and never forward-fill across a missing publication without a flag.

Exposure in home currency at rate `r` is `X_H = X_S*r`. A simulated depreciation of S against H by fraction `q` means `r' = r*(1-q)` and `DeltaX_H = X_S*(r'-r)`. The adverse loss is `max(0, -DeltaX_H)` for a receivable in S; reverse the sign for an S-denominated cost.

For a candidate cross-border booking, expected arbitrage margin is:

`AM = M_home_at_spot - E[FX_loss] - conversion_fees - tax_and_transfer_cost - liquidity_buffer`.

The decision must include a base, adverse, and severe shock, e.g. `q in {0, 0.05, 0.10}` as scenario configuration, not a forecast. Correlated multi-currency exposures use covariance matrix `Sigma` and variance `w' Sigma w`.

### 3.4 Verification

- inverse-rate conversion must round-trip to less than one minor unit after documented rounding;
- a zero exposure has zero FX loss under every scenario;
- a positive S receivable loses home-currency value under S depreciation;
- constant rates produce zero volatility;
- compare computed sample volatility with an independent reference implementation on a frozen ECB fixture;
- reject negative/zero rates, duplicate dates, and dates after retrieval time;
- simulate depreciation and appreciation symmetrically and verify the sign change;
- run rolling out-of-sample volatility calibration and track coverage of realized returns, but do not call scenario output a probability unless calibrated.

## 4. Demographic & Sponsor Portfolio Match

### 4.1 Objective and sources

Use public Reddit subscriber counts and publicly available community demographic summaries where legally and technically permitted. Subscriber count is a scale signal, not unique reach and not a demographic census. Capture subreddit name, retrieval time, visible subscriber count, source URI, and methodology/version of any demographic estimate. Do not collect private user data or infer protected traits about individuals.

### 4.2 Audience and brand schemas

```json
{
  "entity_id":"artist_or_subreddit",
  "as_of":"timestamp",
  "features": {
    "age_18_24":"number >= 0", "age_25_34":"number >= 0",
    "age_35_44":"number >= 0", "age_45_plus":"number >= 0",
    "country_US":"number >= 0", "country_CA":"number >= 0",
    "interest_music":"number >= 0", "interest_outdoor":"number >= 0"
  },
  "feature_semantics":"share|index|count",
  "sample_size":"integer|null",
  "source_quality":"A|B|C"
}
```

Convert each vector to a common nonnegative share space before matching. For feature vector `x`, `x_norm=x/sum(x)` when the sum is positive. Never compare a count vector with an index vector without an explicit transformation.

### 4.3 Cosine similarity and portfolio match

For audience vector `x` and brand profile `b`, cosine similarity is:

`cos(x,b) = (x dot b)/(||x||_2 ||b||_2)`.

If either norm is zero, return null and a quality flag. With feature weights `W=diag(w_k)`, use weighted cosine `cos_W=(x'Wb)/(sqrt(x'Wx)*sqrt(b'Wb))`. Sponsor portfolio match is the exposure-weighted average:

`SPM = sum_j exposure_j*cos_W(x_j,b) / sum_j exposure_j`.

Also report minimum match, dispersion, feature coverage, and sensitivity to each feature family. Similarity is a ranking signal, not evidence that a sponsorship will convert.

### 4.4 Validation and bias controls

- identical nonzero vectors score 1; orthogonal vectors score 0; scaling either vector leaves score unchanged;
- missing features must be masked and renormalized, with coverage reported; never impute a demographic from the artist name;
- compare Reddit subscriber snapshots for monotonicity and flag implausible jumps;
- use bootstrap intervals where demographic estimates have sample sizes; stratify validation by subreddit size and category;
- hold out brands or campaigns by time for ranking validation; report Spearman rank correlation, top-k precision, and calibration if outcome labels exist;
- run a fairness review: exclude protected-attribute targeting where not legally/ethically justified, document proxy features, and require human approval for activation.

## 5. Climate Volatility & Event Disruption Underwriting

### 5.1 Objective and source

Use the Open-Meteo Historical API for hourly/daily historical weather at event coordinates. Store the exact request parameters, model/archive choice, timezone, response hash, and retrieval time. Historical weather is a proxy for future disruption risk and must not be presented as a venue-specific insurance probability without calibration.

### 5.2 Weather/event schema

```json
{
  "event_id":"string",
  "event_date":"date",
  "latitude":"number",
  "longitude":"number",
  "timezone":"IANA timezone",
  "temperature_max_c":"number|null",
  "precipitation_sum_mm":"number|null",
  "weather_source":"open-meteo-historical",
  "weather_source_uri":"string",
  "threshold_version":"string"
}
```

Define event-day indicators:

`H_i = 1[Tmax_i > 35]`; `P_i = 1[precip_i > 25.4]`; `D_i = 1[H_i OR P_i]`.

The historical empirical disruption rate for a comparable set `C` is `p_hat = sum_i D_i / |C|`. A small-sample Bayesian estimate with Beta prior `Beta(a,b)` is `p_tilde=(a+sum D_i)/(a+b+|C|)`, with credible interval documented. Comparable sets should be selected by venue coordinates, calendar month, and, where available, event type; do not mix climates without stratification.

For separate hazards, estimate logistic probabilities:

`logit(p_i)=beta_0 + beta_1*H_i + beta_2*P_i + beta_3*month_i + beta_4*lead_time_i + u_region`.

If the outcome label is future disruption rather than hazard occurrence, train only on observations available before the event. Combine hazard probabilities with dependence: `P(D)=p_H+p_P-P(H and P)`. Do not assume independence unless validated; if only marginal rates exist, report Fréchet bounds `max(0,p_H+p_P-1) <= P(D) <= min(p_H,p_P)`.

Underwriting expected loss is:

`EL = P(D)*severity`,

where severity includes refundable guarantees, replacement production, evacuation/security cost, and lost contribution margin. A reserve may be `EL + z_alpha*sqrt(Var(loss))`, with confidence level and severity distribution explicit. Thresholds are exactly greater-than: heat above 35 C and precipitation above 25.4 mm.

### 5.3 Validation plan

- unit test conversions: 25.4 mm equals 1 inch; 25.4001 mm triggers precipitation risk and 25.4 mm does not under strict `>`;
- exact boundary tests for 35.0 C and 35.0001 C;
- missing weather must yield unknown/null risk, never a safe zero;
- compare a hand-built daily fixture to indicator counts, Beta posterior, and Fréchet bounds;
- verify that UTC/local timezone conversion assigns an hourly observation to the correct event day;
- use rolling-origin backtests, Brier score, log loss, reliability diagrams, calibration slope/intercept, and PR-AUC for rare disruption outcomes;
- test spatial robustness by perturbing coordinates within a documented radius and report score sensitivity;
- stress severe heat, severe rain, and simultaneous hazards separately; every underwriting output must show assumptions, sample size, uncertainty interval, and source hash.

## 6. Cross-module integration and release checklist

A booking decision record joins modules through `event_id`, `artist_id`, and `as_of_utc`, never through display names alone. Each score carries `model_version`, `input_snapshot_ids`, `quality_flags`, and `explanation_components`. Missing or low-quality inputs reduce confidence and may block automated approval.

Before release:

1. freeze fixtures and publish hashes;
2. run schema, unit, property, integration, and leakage tests;
3. verify all external requests obey terms, rate limits, and licensing constraints;
4. compare outputs with an independently implemented calculation for HHI, Haversine, FX conversion/volatility, cosine similarity, and hazard indicators;
5. review threshold and coefficient changes as versioned model changes;
6. produce a validation report containing data coverage, exclusions, calibration, confidence intervals, known bias, and unresolved risks;
7. require human sign-off for agency representation, sponsor activation, currency commitments, and weather-sensitive underwriting.

No module may claim causal impact, guaranteed margin, legal representation status, tradable FX pricing, demographic certainty, or insured loss probability solely from these public-data signals.