# Agency Market Gap and Operations Research Specification

Status: Proposed technical and commercial specification
Audience: Music booking agencies, festival promoters, concert operators, ticketing and sponsorship teams
Primary thesis: Convert historical festival lineups, visual billing, and secondary-market ticket-spread observations into an auditable decision system for talent selection, guarantee negotiation, routing, scheduling, pricing, and risk control.

## 1. Executive summary

Large agencies and promoters already possess valuable data: CRM histories, offers, settlement sheets, ticketing records, venue economics, marketing results, and relationship knowledge. The opportunity is not to claim that they have no analytics. The opportunity is to solve a narrower and more consequential integration problem: make cross-event, cross-promoter, cross-platform market feedback operationally usable at the moment a lineup, guarantee, routing plan, or price is being decided.

The proposed system is an institutional market-intelligence and optimization layer. It joins:

1. Historical festival editions, artist identities, performance status, stage, schedule, and source provenance.
2. Edition-relative visual billing and placement features, including poster rank, tier, typography, and headliner signals.
3. Primary-market outcomes where licensed or supplied by a customer.
4. Secondary-market observations, including SeatGeek-derived listing/spread time series, subject to applicable terms and data rights.
5. Contextual controls: venue capacity, geography, date, genre, festival scale, weather, competing events, marketing window, and artist trajectory.

The product does not promise an “exact” causal value from observational data without qualification. It estimates marginal revenue contribution with confidence intervals, causal controls, sensitivity analysis, and a clear separation between observed resale spread, predicted demand, and incremental event profit.

The economic value proposition is decision quality:

- Promoters reduce over-guaranteeing, empty-capacity risk, schedule bottlenecks, and inefficient stage allocation.
- Agencies defend guarantees with comparable evidence, identify underpriced artists, and improve tour economics for clients.
- Both sides gain a common, auditable vocabulary for negotiation rather than relying only on anecdotes or selectively remembered comps.

## 2. Scope and non-goals

### 2.1 In scope

- Festival and lineup intelligence from 1954 onward, with confidence and provenance.
- Programmatic normalization of artist names, festival editions, billing contexts, stages, and dates.
- Measurement of visual billing position as a market signal, not as an artistic-quality judgment.
- Historical and near-real-time ticket-spread monitoring where data is legally licensed or supplied.
- Forecasting, scenario analysis, mixed-integer optimization, stochastic routing, queueing/simulation, and decision dashboards.
- Multi-tenant controls, data lineage, model versioning, and customer-specific calibration.

### 2.2 Out of scope or requiring explicit entitlement

- Scraping or redistribution that violates a source’s terms, robots directives, copyright, or API license.
- Representing secondary listings as completed sales unless transaction data establishes that fact.
- Inferring private agency guarantees or confidential terms from public evidence.
- Treating a model output as an automatic booking decision. Human approval, commercial judgment, artist-team constraints, and safety review remain required.

## 3. The Booking Agency and Promoter Information Gap

### 3.1 The current operating reality

A relationship-led business is not irrational. Agents know artist teams, availability, soft holds, routing preferences, guarantee history, competing offers, and qualitative momentum that public data cannot see. Promoters know venue operations, local demand, sponsor inventory, production constraints, and audience behavior. Major organizations also operate ticketing, CRM, marketing, finance, and event systems.

The gap is therefore not “data versus no data.” It is the absence of a neutral, provenance-preserving, decision-grade layer that joins the following questions in one workflow:

- What did this artist’s visual billing position communicate in comparable festivals?
- How did primary sell-through and secondary-market spread respond after announcement, on-sale, lineup additions, and event week?
- What was the artist’s incremental contribution after controlling for festival scale, other artists, date, market, price, and scarcity?
- What guarantee is supportable under downside scenarios?
- Which lineup, route, stage assignment, and price ladder maximize risk-adjusted contribution jointly?

### 3.2 Legacy heuristics and their failure modes

Common heuristics include recent ticket counts, streaming or social momentum, promoter memory, agent-reported comps, prior guarantees, perceived “festival fit,” and the visible order of a poster. These signals can be useful but are vulnerable to:

- Selection bias: only booked artists generate observed festival outcomes.
- Survivorship bias: famous, well-archived editions dominate historical comparisons.
- Confounding: a headliner’s result is entangled with the rest of the lineup, venue, marketing, and macro demand.
- Time decay: a guarantee or draw from a prior cycle may be stale.
- Context mismatch: an arena headline, a tent close, and a mid-card festival slot are not equivalent observations.
- Siloing: ticketing, sponsorship, operations, agency CRM, and historical research are not represented in one feature store.
- Negotiation opacity: each side sees part of the market and may have different incentives to characterize demand.
- Slow feedback: a post-event settlement may not be joined to the exact billing and market signals that preceded the decision.

The product should never assert that CAA, WME, UTA, Live Nation, AEG, or C3 lacks internal systems. Public evidence confirms that C3 describes in-house creative, marketing, sponsorship, ticketing, and production teams, and that Ticketmaster states event organizers set face value while considering production cost, venue size, and interest. These facts make the integration opportunity more credible, not less: the proposed layer can sit above or beside existing systems and create cross-platform comparability rather than replace them.

### 3.3 Exact market gap

The market gap is a cross-organizational event-demand intelligence and optimization system with five properties:

1. Historical depth: it remembers how billing, artist trajectory, and event context evolved over decades.
2. Cross-platform linkage: it connects lineup structure to primary demand, secondary spreads, concessions, sponsorship, and operational outcomes.
3. Causal discipline: it distinguishes correlation, prediction, and incremental contribution.
4. Optimization: it turns predictions into lineup, route, schedule, guarantee, and price decisions subject to hard constraints.
5. Auditability: every feature and recommendation has source lineage, uncertainty, version, and scenario assumptions.

This is the transition from a hobby database to an enterprise system of decision support. The defensible asset is not merely a list of artists. It is the normalized event graph, the historical feature store, the outcome joins, the calibrated models, and the feedback loop generated by customer decisions and realized results.

### 3.4 Feedback-loop architecture

For event e, collect an event snapshot at decision time t:

\[
X_{e,t} = [\text{lineup},\ \text{billing},\ \text{price ladder},\ \text{capacity},\ \text{marketing},\ \text{market},\ \text{calendar},\ \text{weather},\ \text{inventory}].
\]

Observe outcomes at multiple horizons:

\[
Y_e = [\text{announcement velocity},\ \text{on-sale velocity},\ \text{sell-through},\ \text{resale spread},\ \text{attendance},\ \text{F\&B per head},\ \text{sponsor value},\ \text{settlement margin},\ \text{safety incidents}].
\]

The feature store must preserve point-in-time availability. A model used on January 10 may only use data known on January 10. Every prediction is stored as:

\[
\hat{Y}_{e,t}^{(m)} = f_m(X_{e,\le t};\ \theta_m,\ \text{version}),
\]

with realized outcome, residual, calibration status, and model drift recorded after the event. This makes learning continuous without leaking future information into historical backtests.

## 4. Data model and analytical contract

The underlying historical specification already defines festival, festival_edition, festival_stage, festival_performance, billing_observation, source, source_assertion, artist aliases, poster assets, and derived billing metrics. The enterprise layer extends it with:

- event_outcome(event_id, observation_time, metric, value, unit, source, confidence);
- ticket_observation(event_id, platform, observed_at, section, price, fees, quantity, listing_status, provenance);
- primary_inventory(event_id, observed_at, price_bucket, available_count, sold_count, capacity);
- artist_context(artist_id, date, venue_scale, territory, genre, release_cycle, availability);
- offer_snapshot(artist_id, event_id, guarantee, backend_terms, currency, date, source, confidentiality_class);
- model_prediction(event_id, model_version, horizon, metric, prediction, lower_bound, upper_bound);
- optimization_run(run_id, objective, constraints, input_snapshot_hash, solver, status, solution, duals, created_at);
- decision_outcome(decision_id, recommendation_id, accepted, override_reason, realized_result).

Data-quality rules:

- Use immutable raw observations and versioned derived features.
- Distinguish listed price, all-in price, realized transaction price, and spread.
- Store currency, timezone, timestamp, and observation horizon.
- Treat copied sources as correlated evidence.
- Require a minimum completeness score before a recommendation is labeled decision-grade.
- Preserve customer-private data in tenant-isolated schemas with row-level access control.

## 5. Operations Research role

Operations Research is the formal discipline for selecting actions when resources are scarce, objectives conflict, and uncertainty matters. A festival is a coupled system:

- Artists are projects consuming budget, stage minutes, technical capacity, and calendar availability.
- Stages and venues are capacity-constrained resources.
- Travel is a network-flow problem with time windows and failure risk.
- Audience movement is a dynamic system whose congestion affects safety and monetization.
- Ticket pricing is a yield-management problem under uncertain demand.

The OR layer should not replace forecasting. Forecasting estimates distributions; OR chooses a feasible portfolio under those distributions. A practical architecture is:

1. Estimate demand and financial distributions.
2. Generate feasible candidate lineups/routes/schedules.
3. Optimize expected or risk-adjusted value.
4. Stress-test with scenarios and simulations.
5. Present shadow prices, tradeoffs, and binding constraints to decision-makers.

## 6. Lineup construction: multi-constraint knapsack / integer programming

### 6.1 Sets and parameters

Let:

- \(A\): candidate artists; \(S\): stages; \(D\): event days; \(G\): genres or audience segments.
- \(x_{a,s,d}\in\{0,1\}\): artist a is assigned to stage s on day d.
- \(y_a\in\{0,1\}\): artist a is selected.
- \(b_a\): guarantee, buyout, or expected artist cost.
- \(\tau_a\): set duration including changeover allocation.
- \(c_s\): usable capacity in stage-minutes.
- \(B\): artist budget.
- \(q_{a,g}\): audience affinity of artist a for segment/genre g.
- \(L_g,U_g\): lower and upper target for segment g.
- \(r_a(\omega)\): random ticket contribution under scenario \(\omega\).
- \(f_a(\omega)\): expected concession/F&B contribution.
- \(z_a(\omega)\): sponsorship or brand-attraction contribution.
- \(k_a\): technical, production, and hospitality cost.
- \(v_{a,s,d}\): feasibility indicator for artist, stage, and day.
- \(h_{a,s,d}\): billing value, derived from historical visual and schedule features.

Define net event contribution in scenario \(\omega\):

\[
\pi_a(\omega)=r_a(\omega)+f_a(\omega)+z_a(\omega)-b_a-k_a.
\]

### 6.2 Risk-adjusted objective

A robust objective can maximize expected contribution minus downside risk:

\[
\max \quad \mathbb{E}_\omega[\Pi(x,\omega)] - \lambda\operatorname{CVaR}_{\alpha}(-\Pi(x,\omega)) + \rho\sum_{a,s,d}h_{a,s,d}x_{a,s,d},
\]

where \(\lambda\ge0\) controls risk aversion, \(\rho\) controls billing/brand value, and \(\operatorname{CVaR}_{\alpha}\) penalizes the worst \(1-\alpha\) tail.

For finite scenarios, introduce \(\eta\) and \(u_\omega\ge0\):

\[
\operatorname{CVaR}_{\alpha}(L)=\eta+\frac{1}{1-\alpha}\sum_{\omega}p_\omega u_\omega,
\]

\[
u_\omega \ge L_\omega-\eta, \qquad L_\omega=-\Pi(x,\omega).
\]

This makes downside protection explicit rather than hiding it in a point forecast.

### 6.3 Core constraints

Selection consistency:

\[
\sum_{s,d}x_{a,s,d}=y_a \qquad \forall a\in A.
\]

Stage-time capacity:

\[
\sum_{a,d}\tau_a x_{a,s,d}\le c_s \qquad \forall s\in S.
\]

Budget:

\[
\sum_a b_a y_a + \sum_{a,s,d}k_{a,s,d}x_{a,s,d}\le B.
\]

Genre/demographic targets:

\[
L_g\le\sum_{a,s,d}q_{a,g}x_{a,s,d}\le U_g \qquad \forall g\in G.
\]

Stage capacity and artist compatibility:

\[
x_{a,s,d}\le v_{a,s,d}; \qquad \sum_a x_{a,s,d}\le n_{s,d},
\]

where \(n_{s,d}\) is the maximum number of sets.

Billing constraints can be modeled with tier variables \(w_{a,k}\in\{0,1\}\), where k is a tier:

\[
\sum_k w_{a,k}=y_a,
\]

\[
\sum_{a}w_{a,\text{headliner}}=H,
\]

\[
\sum_{a\in G_0}w_{a,\text{headliner}}\ge H_0,
\]

and pairwise incompatibility or exclusivity constraints:

\[
x_{a,s,d}+x_{a',s',d}\le 1 \quad \text{if a and a' cannot overlap or share a required audience resource}.
\]

A minimum headline separation constraint can be imposed through time-indexed slots \(t\):

\[
\sum_{a\in H}x_{a,s,t}\le 1 \qquad \forall s,t,
\]

or by requiring selected headline acts to occupy distinct principal windows.

### 6.4 Marginal value and sensitivity

The solver should expose shadow prices for continuous constraints. For budget \(B\), the dual value \(\mu_B\) approximates the value of one additional budget unit. For stage time \(c_s\), \(\mu_s\) identifies the economic value of one additional stage-minute. These values directly support decisions such as adding a stage, shortening changeovers, or reallocating a guarantee pool.

The marginal contribution of artist a is not simply \(\pi_a\). It is the difference between optimized portfolio value with and without a:

\[
\Delta_a = V(A)-V(A\setminus\{a\}),
\]

or, when lineup interactions matter, the Shapley-style average over sampled coalitions:

\[
\phi_a=\frac{1}{|A|!}\sum_{\sigma}[V(P_a^\sigma\cup\{a\})-V(P_a^\sigma)],
\]

where \(P_a^\sigma\) is the set preceding a in permutation \(\sigma\). This is computationally expensive but valuable for explaining complementarity and cannibalization.

## 7. Stochastic touring routing and network flows

### 7.1 Network model

Let venues or markets be nodes \(V\), directed travel legs be arcs \(E\), and dates be \(T\). Let \(x_{i,j,t}=1\) if the route travels from market i to j between shows on dates t and t+1. Let \(y_{i,t}=1\) if a show occurs at i on t.

Flow conservation:

\[
\sum_j x_{j,i,t-1}=y_{i,t}=\sum_jx_{i,j,t} \qquad \forall i,t,
\]

with suitable start/end depot constraints.

Each arc has travel cost \(c_{ij}\), travel time \(\ell_{ij}\), emissions or crew burden \(e_{ij}\), and disruption probability \(p_{ij}\). Each venue-date has expected contribution \(R_{i,t}(\omega)\), capacity, and venue-size risk.

### 7.2 Objective

\[
\max \mathbb{E}_\omega\left[\sum_{i,t}R_{i,t}(\omega)y_{i,t} - \sum_{i,j,t}c_{ij}x_{i,j,t}\right]
-\lambda\operatorname{CVaR}_{\alpha}(\text{loss})
-\gamma\sum_{i,t}\text{SizeRisk}_{i,t}y_{i,t}.
\]

Size risk can be defined as expected unused capacity:

\[
\text{SizeRisk}_{i,t}=\mathbb{E}\left[\max(0,K_i-D_{i,t})\right],
\]

where \(K_i\) is capacity and \(D_{i,t}\) is stochastic demand. A mismatch penalty can also be quadratic in a continuous relaxation:

\[
\text{Mismatch}_{i,t}=\left(K_i-\mathbb{E}[D_{i,t}]\right)^2.
\]

### 7.3 Date conflicts, rest, and availability

If artist a is on a route, no two shows may violate minimum rest \(R_a\):

\[
\text{start}_{a,t_2}-\text{end}_{a,t_1}\ge R_a - M(2-y_{a,t_1}-y_{a,t_2})
\]

for all incompatible date pairs, with large constant M. Availability is enforced by \(y_{a,t}\le avail_{a,t}\). Travel feasibility requires:

\[
\text{distance}_{ij}/v_{\text{effective}} + \text{buffer}_{ij}
\le \text{available time}_{t} + M(1-x_{i,j,t}).
\]

### 7.4 Practical solution approach

Use a mixed-integer model for the committed core route and stochastic programming or Monte Carlo simulation for weather, delays, demand, and cancellations. For large tours, use decomposition: master problem selects markets/dates; subproblems check crew, travel, and venue feasibility. Report route alternatives, incremental margin per travel day, probability of missed show, and the value of preserving a rest or recovery day.

## 8. Queueing theory and crowd dynamics for set scheduling

### 8.1 Audience movement model

For each stage s, let arrivals be a time-varying process with rate \(\lambda_s(t)\). Let service capacity represent the rate at which audience members can enter, exit, purchase, or pass through a zone, \(\mu_s(t)\). A basic queue has utilization:

\[
\rho_s(t)=\frac{\lambda_s(t)}{\mu_s(t)}.
\]

For an M/M/1 approximation with \(\rho<1\):

\[
L_s=\frac{\rho_s}{1-\rho_s}, \qquad W_s=\frac{1}{\mu_s-\lambda_s}.
\]

For multiple service channels, use M/M/c or a network of queues. Concession throughput can be modeled as a tandem or multi-class queue: entry, circulation, bar/food service, restroom, and exit. Since festival arrivals are not truly Poisson and behavior changes around set transitions, use a Markov-modulated arrival process or discrete-event simulation for final validation.

### 8.2 Markov audience-state transitions

Partition attendees into states such as stage A, stage B, concession, restroom, transit, and exit. Let \(p_i(t)\) be the state distribution and \(Q(t)\) the generator matrix:

\[
\frac{d p(t)}{dt}=p(t)Q(t), \qquad \sum_i p_i(t)=1.
\]

Transition rates should depend on set changes, artist affinity, walking distance, weather, and observed crowd conditions. If \(m_i(t)\) is occupancy in zone i and \(K_i\) is safe practical capacity, enforce:

\[
\Pr[m_i(t)>K_i]\le \epsilon_i \qquad \forall i,t.
\]

A fluid approximation is:

\[
\dot m_i(t)=\sum_j q_{ji}(t)m_j(t)-\sum_jq_{ij}(t)m_i(t)+a_i(t)-d_i(t).
\]

### 8.3 Set-scheduling objective

Let \(R_{s,t}\) be expected revenue-per-head from stage s at time t, \(B_{s,t}\) be bottleneck risk, and \(C_{s,s',t}\) be sound-bleed or audience-conflict penalty for simultaneous sets. Then:

\[
\max \sum_{s,t}R_{s,t}x_{s,t}
-\alpha\sum_{i,t}\mathbb{E}[\max(0,m_i(t)-K_i)^2]
-\beta\sum_{s\ne s',t}C_{s,s',t}x_{s,t}x_{s',t}.
\]

The bilinear term can be linearized with auxiliary variables \(z_{s,s',t}\):

\[
z_{s,s',t}\le x_{s,t},\quad z_{s,s',t}\le x_{s',t},\quad
z_{s,s',t}\ge x_{s,t}+x_{s',t}-1.
\]

The optimization should jointly consider attendance and monetization. Maximizing bar throughput alone can produce an unsafe schedule; minimizing movement alone can destroy revenue. The correct objective is risk-constrained contribution with explicit safety thresholds.

## 9. Yield optimization and price elasticity analytics

### 9.1 Definitions

For event e and observation time t, define:

- \(P^{(1)}_{e,t}\): primary all-in price or weighted average primary price.
- \(P^{(2)}_{e,t}\): comparable secondary-market median or quantile price.
- \(S_{e,t}=P^{(2)}_{e,t}-P^{(1)}_{e,t}\): absolute spread.
- \(M_{e,t}=P^{(2)}_{e,t}/P^{(1)}_{e,t}-1\): percentage spread.
- \(V_{e,t}\): available inventory or listing depth.
- \(Q_{e,t}\): tickets sold or observed demand proxy.

Use multiple quantiles because a single mean is fragile:

\[
S^{(q)}_{e,t}=Q_q(P^{(2)}_{e,t})-P^{(1)}_{e,t}, \qquad q\in\{0.25,0.50,0.75\}.
\]

A spread is a market signal, not automatically promoter revenue. It may reflect scarcity, seller behavior, fees, speculative listings, or unobserved primary inventory.

### 9.2 Elasticity model

Let \(b_{a,e}\) be normalized billing position, \(H_{a,e}\) headliner status, and \(Z_e\) controls. Model log demand or spread:

\[
\log Q_{e,t}=\alpha_e+\delta_t+\beta_1\log P_{e,t}
+\beta_2 b_{a,e}+\beta_3 H_{a,e}
+\beta_4(\log P_{e,t}\times b_{a,e})+\gamma^TZ_e+\varepsilon_{e,t}.
\]

The price elasticity is:

\[
\epsilon_{e,t}=\frac{\partial\log Q_{e,t}}{\partial\log P_{e,t}}
=\beta_1+\beta_4b_{a,e}.
\]

If the objective is resale spread rather than quantity, estimate:

\[
M_{e,t}=\alpha+\beta b_{a,e}+\gamma H_{a,e}+\delta\text{FestivalScale}_e
+\eta\text{Scarcity}_{e,t}+\theta^TZ_e+u_e+\varepsilon_{e,t}.
\]

Use event and market fixed effects, time-to-event splines, lineup controls, and clustered errors. A stronger causal design uses lineup announcement or artist addition as an event study:

\[
Y_{e,t}=\alpha_e+\delta_t+\sum_{k\ne -1}\tau_k\mathbf{1}[t-T_e=k]
+\gamma^TZ_{e,t}+\varepsilon_{e,t}.
\]

The coefficients \(\tau_k\) estimate dynamic changes relative to the omitted pre-event period, subject to parallel-trend diagnostics.

### 9.3 Marginal revenue contribution

For artist a, define a counterfactual prediction with and without the artist while holding the rest of the event fixed:

\[
\Delta\widehat{R}_a
=\sum_{t}N_{e,t}\left[\widehat{p}_{e,t}(X_e)-\widehat{p}_{e,t}(X_e\setminus a)\right]
+\Delta\widehat{F\&B}_a+\Delta\widehat{Sponsor}_a-\Delta Cost_a.
\]

Here \(N_{e,t}\) is relevant inventory, and \(\widehat p\) is predicted net ticket yield or purchase probability. The model must include interaction terms because the marginal contribution of an artist depends on who else is on the lineup:

\[
\Delta\widehat{R}_a(X)=\widehat{R}(X\cup\{a\})-\widehat{R}(X).
\]

Report a confidence interval generated by bootstrap, Bayesian posterior, or scenario simulation. Do not report a single “exact” number when the data cannot identify it.

Agency use: negotiate a guarantee up to a risk-adjusted share of \(\Delta\widehat R_a\), with contract-specific backend and uncertainty. Promoter use: compare guarantee plus incremental cost against downside-tail contribution and hedge through price tiers, inventory release, or lineup substitution.

### 9.4 Bias, validation, and leakage controls

- Separate announced lineup from final performed lineup.
- Control for billing position as a potentially endogenous choice.
- Use holdout festivals and future-period validation.
- Winsorize or model extreme resale values separately.
- Distinguish listing snapshots from completed sales.
- Avoid using post-announcement data in a pre-announcement recommendation.
- Test whether a model merely recognizes the festival brand or venue rather than the artist.
- Publish calibration, coverage, confidence, and missingness next to every estimate.

## 10. Enterprise product specification

### 10.1 API surface

All endpoints require tenant authentication, role-based authorization, pagination, idempotency keys for writes, and a response envelope containing data_version and provenance metadata.

Core entities:

- GET /v1/artists/{artist_id}
- GET /v1/artists/{artist_id}/trajectory
- GET /v1/artists/{artist_id}/comparables
- GET /v1/festivals/{festival_id}/editions
- GET /v1/editions/{edition_id}/lineup
- GET /v1/editions/{edition_id}/billing
- POST /v1/lineups/score
- POST /v1/lineups/optimize
- POST /v1/routes/optimize
- POST /v1/schedules/simulate
- POST /v1/pricing/forecast
- GET /v1/events/{event_id}/ticket-spread
- GET /v1/events/{event_id}/marginal-contribution
- GET /v1/models/{model_id}/predictions
- GET /v1/optimization-runs/{run_id}
- GET /v1/sources/{source_id}
- POST /v1/feedback/outcomes

Customer data ingestion:

- POST /v1/ingest/ticketing
- POST /v1/ingest/settlements
- POST /v1/ingest/offers
- POST /v1/ingest/attendance
- POST /v1/ingest/concessions
- POST /v1/ingest/sponsorship

Every ingestion record should support effective_at, observed_at, source_system, schema_version, and correction_of. Never overwrite a prior customer observation without preserving the correction chain.

### 10.2 Dashboards

1. Agency portfolio dashboard: artist trajectory, festival tier history, territory heatmap, guarantee comps, predicted marginal contribution, confidence, and negotiation range.
2. Promoter lineup lab: drag-and-drop lineup alternatives with budget, stage minutes, demographic balance, forecast sell-through, F&B, sponsor value, CVaR, and binding constraints.
3. Ticket yield console: primary inventory, secondary spread quantiles, time-to-event curves, price ladder, release recommendations, and anomaly flags.
4. Route planner: map, travel cost, rest violations, expected margin per leg, venue-size risk, and disruption scenarios.
5. Schedule and crowd control: stage occupancy forecast, transitions, predicted flows, queue utilization, sound-bleed conflicts, and safety constraint violations.
6. Evidence and governance view: source URLs, poster regions, OCR/CV confidence, model version, feature lineage, and analyst overrides.
7. Post-event learning dashboard: predicted versus realized demand, calibration, forecast error by genre/market, and ROI of recommendations.

### 10.3 Predictive and optimization tooling

Minimum viable models:

- Artist identity resolution with human review queue.
- Billing hierarchy normalization and confidence scoring.
- Ticket velocity forecast by event horizon.
- Secondary spread quantile forecast.
- Demand elasticity and cannibalization model.
- F&B per-capita forecast by lineup and schedule.
- Venue-size and sell-through risk model.
- Route disruption and travel-cost model.
- Crowd-flow discrete-event simulator.
- Mixed-integer lineup and schedule solver with scenario mode.

Enterprise requirements:

- Point-in-time feature store and reproducible backtests.
- Model registry with champion/challenger deployment.
- Data lineage and source-level audit trails.
- Explainable recommendations: drivers, constraints, counterfactuals, and uncertainty.
- Customer-specific calibration without cross-tenant data leakage.
- SSO/SAML, SCIM, RBAC, audit logs, encryption, retention policies, and export/delete controls.
- Solver time limits, incumbent solutions, optimality gaps, infeasibility diagnostics, and human-editable constraints.

## 11. Commercial packaging and defensibility

Possible products:

- Agency Intelligence: artist valuation, market comps, billing trajectory, and guarantee support.
- Promoter Decision Cloud: lineup optimization, routing, yield, schedule, and event-risk management.
- Data API: normalized historical festival and billing features for internal systems.
- Benchmarking and advisory: custom models using customer settlement and ticketing outcomes.

Defensibility compounds through:

1. Clean historical entity resolution and edition graph.
2. Rare visual-billing measurements with source provenance.
3. Longitudinal ticket-spread observations aligned to lineup change dates.
4. Customer feedback loops linking recommendations to realized outcomes.
5. Optimization models embedded in daily workflows rather than a static report.
6. Calibrated uncertainty and governance trusted by finance, legal, operations, and talent teams.

Do not sell “AI guesses.” Sell measurable reduction in forecast error, guarantee leakage, unused capacity, travel cost, schedule risk, and time spent assembling negotiation evidence.

## 12. Implementation roadmap and acceptance criteria

Phase 1: data foundation. Normalize the existing schema, source assertions, artist aliases, billing observations, and ticket snapshots. Acceptance: reproducible event-level feature table with source lineage and no silent overwrites.

Phase 2: analytical baseline. Implement billing score, trajectory metrics, spread quantiles, ticket velocity, and comparable-event reports. Acceptance: temporal holdout evaluation, confidence intervals, and leakage tests.

Phase 3: decision prototypes. Implement lineup IP, route optimization, schedule simulation, and counterfactual artist removal. Acceptance: feasible solutions, solver status, constraints, sensitivity outputs, and analyst override logging.

Phase 4: enterprise integrations. Ingest customer ticketing, settlement, attendance, concession, sponsorship, and offer data. Acceptance: tenant isolation, point-in-time snapshots, reconciliation reports, and correction history.

Phase 5: feedback and causal calibration. Run post-event evaluation, event studies, uplift/counterfactual tests, and model recalibration. Acceptance: documented error by market and event type, drift alerts, and recommendation ROI.

Phase 6: production controls. Add SSO, RBAC, audit, data retention, source-rights controls, rate limits, observability, disaster recovery, and service-level objectives.

Success metrics should include:

- Reduction in absolute forecast error for sell-through and ticket velocity.
- Reduction in guarantee-to-realized-contribution variance.
- Improvement in expected contribution at fixed risk.
- Reduction in unused capacity and travel overhead.
- Reduction in crowd bottleneck exposure and schedule violations.
- Time saved from research to approved offer or lineup scenario.
- Percentage of decisions with complete evidence lineage.

## 13. Caveats and governance

The market is relationship-intensive and partially private. Public data can establish market context but cannot reveal every offer, guarantee, hold, or strategic constraint. Secondary-market spread can be informative without being a direct measure of promoter profit. Visual billing is an observable signal of expected value and negotiation intent, not a pure measure of demand. Historical coverage will be biased toward famous, English-language, and well-archived festivals.

Accordingly, every production recommendation must display:

- data cutoff and observation horizon;
- source and rights status;
- model version and training window;
- prediction interval and calibration;
- assumptions and excluded variables;
- counterfactual definition;
- binding constraints and sensitivity;
- human owner and override reason.

The strategic position is strongest when the product is framed as an auditable decision-support and optimization layer that augments, rather than dismisses, the expertise of agents, promoters, finance teams, and production teams.

## 14. Research references

- Live Nation Entertainment, 2022 annual report and risk disclosures: https://investors.livenationentertainment.com/sec-filings/annual-reports/content/0001335258-23-000014/lyv-20221231.htm
- C3 Presents, company capabilities: https://www.c3presents.com/
- United Talent Agency, music practice: https://www.unitedtalent.com/talent/music
- Ticketmaster Help, “How are ticket prices and fees determined?”: https://help.ticketmaster.com/hc/en-us/articles/9663528775313-How-are-ticket-prices-and-fees-determined
- SeatGeek support, API and data-use guidance: https://support.seatgeek.com/hc/en-us/articles/4409765051283-Can-I-Use-SeatGeek-Data-or-an-API
- IBM Decision Optimization, constraint programming overview: https://ibmdecisionoptimization.github.io/docplex-doc/cp.html
- Historical festival database and provenance specification in this repository: docs/historical_festival_artist_database_spec.md
- Historical lineup and billing analysis in this repository: docs/historical_lineups_and_billing_analysis.md
- Secondary-market ticket research in this repository: docs/secondary_market_ticket_arbitrage_research.md

