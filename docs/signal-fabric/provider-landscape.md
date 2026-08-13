# Signal Fabric — Provider & Model Landscape

Research reference for the Festival Signal Fabric. Documenting a technology
here does **not** mean it is installed or production-ready; integration is a
separate, benchmarked decision (see `docs/acquisition-provider-benchmarks.md`).
This is a research document, not an approval list.

---

## Acquisition technologies

### Monid
- **Purpose:** managed discovery/execution layer for public-data providers (X, Reddit, TikTok, Instagram, YouTube, LinkedIn, etc.)
- **Project / repo:** https://docs.monid.ai — closed platform, current API: `POST /v1/discover`, `POST /v1/inspect`, `POST /v1/run`, `GET /v1/runs/:runId`, `GET /v1/wallet/balance`
- **License / terms:** commercial SaaS terms; credential via `MONID_API_KEY`
- **Maintained:** yes (actively updated API)
- **Best use case:** find and run a specialized provider without owning the collector
- **Limitations:** async runs need polling; per-endpoint pricing varies; provider reliability must be measured empirically
- **Policy considerations:** underlying platform terms still apply — Monid is a *managed provider*, not the originating source
- **Integration status:** canonical provider implemented in `python/festival_bloomberg/acquisition/providers/monid.py` (fixture-tested, offline)

### Apify
- **Purpose:** serverless, repeatable "Actor" scraping programs callable by API
- **Project / repo:** https://docs.apify.com/api/v2/actors
- **License / terms:** commercial platform; credential via `APIFY_TOKEN`; per-run USD costs
- **Maintained:** yes
- **Best use case:** approved, specialized collectors someone already maintains
- **Limitations:** cost per run; Actor availability varies; dataset schema is per-Actor
- **Policy considerations:** an Actor scrapes an underlying platform — that platform's terms still govern; "Apify" is never the originating source
- **Integration status:** canonical provider implemented (`apify.py`, fixture-tested, offline)

### Scrapling (D4Vinci)
- **Purpose:** adaptive web scraping — selectors that relocate after page changes, static/dynamic fetching, crawling, proxies, AI-targeted extraction
- **Project / repo:** https://github.com/D4Vinci/Scrapling (AGPL-3.0)
- **Maintained:** yes (actively maintained)
- **Best use case:** dynamic public pages for approved sources; agent extraction with prompt-injection protections
- **Limitations:** heavier dependency (browser engines for dynamic mode); AGPL licensing must be reviewed before embedding in a commercial product
- **Policy considerations:** scraped content is untrusted data; never injected into agent prompts as instructions
- **Integration status:** optional provider implemented (`scrapling.py`); reports `NOT_CONFIGURED` when the package is not installed — deliberately not a hard dependency

### Crawlee Python (Apify)
- **Purpose:** open-source crawler framework: raw HTTP, Parsel/BeautifulSoup, Playwright, persistent state, storage, proxies
- **Project / repo:** https://github.com/apify/crawlee-python (Apache-2.0)
- **Maintained:** yes
- **Best use case:** larger crawl queues/state/retries when a self-hosted collector is justified
- **Limitations:** operational overhead (queues, storage, retry semantics)
- **Policy considerations:** same as any self-hosted scraping — per-source policy gates required
- **Integration status:** not implemented; benchmark before adopting

### Scrapy
- **Purpose:** mature structured crawling engine
- **Project / repo:** https://github.com/scrapy/scrapy (BSD-3-Clause)
- **Maintained:** yes
- **Best use case:** large, predictable sites with stable structure
- **Limitations:** weaker against aggressive anti-bot changes; template code needs real selectors (the old `intelligence/festival_scraper` spider was template-only)
- **Policy considerations:** per-source policy gates required
- **Integration status:** not implemented; the legacy template spider was removed with the `intelligence/` subtree

### Playwright
- **Purpose:** browser automation for JS-rendered pages
- **Project / repo:** https://github.com/microsoft/playwright (Apache-2.0)
- **Maintained:** yes
- **Best use case:** browser-only sources, used sparingly
- **Limitations:** expensive per page; detectable as automation
- **Policy considerations:** scraping terms of the target site still apply
- **Integration status:** not implemented; available through Crawlee/Scrapling where needed

### Crawl4AI
- **Purpose:** LLM/RAG-ready structured content extraction from pages
- **Project / repo:** https://github.com/unclecode/crawl4ai (Apache-2.0)
- **Maintained:** yes (with a notable security history — isolate and pin before use)
- **Best use case:** unstructured web → clean documents for later NLP
- **Limitations:** should be isolated from the trusted agent process
- **Policy considerations:** prompt-injection exposure — treat all output as untrusted
- **Integration status:** not implemented

### YouTube Data API (official)
- **Purpose:** official videos/comments/statistics access
- **Project / repo:** https://developers.google.com/youtube/v3 (Google ToS; quota-based free tier)
- **Maintained:** yes (official)
- **Best use case:** first high-integrity official social source (videos + comment threads)
- **Limitations:** quota units (search=100/videos=1/commentThreads=1), no historical engagement
- **Policy considerations:** YouTube ToS; commercial use requires review (profile: `LEGAL_REVIEW_REQUIRED`)
- **Integration status:** canonical provider implemented (`youtube.py`, fixture-tested, offline)

### yt-dlp
- **Purpose:** video metadata/subtitles where permitted
- **Project / repo:** https://github.com/yt-dlp/yt-dlp (Unlicense)
- **Maintained:** yes
- **Best use case:** optional media provider for captions/subtitles on permitted content
- **Limitations:** ToS/legal risk per site; must be policy-gated and optional
- **Policy considerations:** only where permitted; not a blanket production dependency
- **Integration status:** not implemented

### Whisper (OpenAI)
- **Purpose:** open multilingual speech recognition (captions → text NLP)
- **Project / repo:** https://github.com/openai/whisper (MIT)
- **Maintained:** yes (successor models released)
- **Best use case:** transcribing permitted audio/video evidence
- **Limitations:** heavy compute; language coverage varies
- **Policy considerations:** only process media the project is permitted to process
- **Integration status:** not implemented

---

## ML / NLP technologies

### VADER
- **Task:** lexicon sentiment baseline
- **License:** MIT
- **Model/version:** `vaderSentiment` 3.3.x (in `requirements.txt`)
- **Training domain:** general social text
- **Languages:** English
- **Inference cost:** negligible (lexicon)
- **Evaluation requirement:** baseline only; compare against social-transformer outputs on labeled fixtures

### TweetNLP
- **Task:** social-media specialized transformer NLP (sentiment, emotion, irony, offensive language, topic)
- **License:** model weights vary (check per model); library MIT/Apache per component
- **Model/version:** e.g. `cardiffnlp/twitter-roberta-base-sentiment-latest` (experimental in this repo: `tweetnlp-bertweet-sentiment` v0.5)
- **Training domain:** Twitter/X text
- **Languages:** English (+ multilingual variants)
- **Inference cost:** GPU-friendly transformer; heavier than VADER
- **Evaluation requirement:** evaluate on labeled live-entertainment text before use; currently a lazy-import baseline (reports `NOT_AVAILABLE` when uninstalled)

### XLM-T
- **Task:** multilingual social-media sentiment
- **License:** Apache-2.0 (weights per model)
- **Model/version:** `cardiffnlp/xlm-twitter` family
- **Training domain:** multilingual Twitter
- **Languages:** ~12 (ar, en, fr, de, hi, it, nl, pt, ru, es, zh, etc.)
- **Inference cost:** transformer; higher than VADER
- **Evaluation requirement:** multilingual evaluation before use

### Sentence Transformers
- **Task:** embeddings, semantic dedup, clustering, comparable retrieval
- **License:** Apache-2.0
- **Model/version:** e.g. `all-MiniLM-L6-v2` (research)
- **Training domain:** general text
- **Languages:** English (+ multilingual variants)
- **Inference cost:** moderate
- **Evaluation requirement:** benchmark embedding quality on dedup/retrieval tasks

### BERTopic
- **Task:** dynamic topic discovery
- **License:** MIT
- **Model/version:** BERTopic latest
- **Training domain:** n/a (pipeline over embeddings)
- **Languages:** depends on embedding model
- **Inference cost:** moderate
- **Evaluation requirement:** topic coherence eval on festival/social corpora

---

## Source licensing notes (registry statuses)

| Source | Content license | API access | Commercial product | Registry state |
|---|---|---|---|---|
| Wikidata | CC0 | open | APPROVED | `APPROVED` |
| Wikimedia Analytics | varies by project (CC-BY-SA) | open | ATTRIBUTION | `APPROVED_WITH_CONDITIONS` |
| GDELT | CC BY 4.0 | open | ATTRIBUTION | `APPROVED_WITH_CONDITIONS` |
| MusicBrainz web service | CC BY-SA (DB dump) | free for non-commercial | REVIEW | `LEGAL_REVIEW_REQUIRED` |
| YouTube Data API | ToS | quota | REVIEW | `LEGAL_REVIEW_REQUIRED` |
| Ticketmaster Discovery | proprietary | agreement | AGREEMENT | `COMMERCIAL_AGREEMENT_REQUIRED` |
| Reddit / X / TikTok / IG / FB | contested/ToS | ToS | REVIEW | `LEGAL_REVIEW_REQUIRED` |

`UNKNOWN` fails closed in every mode. Commercial approval is an explicit
registry state — never inferred from model output.
