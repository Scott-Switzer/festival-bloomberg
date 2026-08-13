# Festival Bloomberg Consolidation Manifest

**Date**: August 12, 2026  
**Repository**: https://github.com/Scott-Switzer/festival-bloomberg  
**Legacy Repository**: https://github.com/Scott-Switzer/festival-intelligence  
**Purpose**: Inventory and classification of all components for consolidation into canonical codebase

---

## Executive Summary

The current repository contains two codebases that need consolidation:
1. **Main festival-bloomberg repository** - Production-focused code with backtesting, portfolio analytics, and ticket spread tracking
2. **intelligence/ subtree** - Legacy festival-intelligence repository with comprehensive scraping, entity resolution, and warehouse functionality

The consolidation strategy is to preserve valuable functionality from both while eliminating redundancy and establishing a single canonical architecture.

---

## Repository Structure Analysis

### Main Repository (festival-bloomberg)

**Current State**: 
- TypeScript/JavaScript focus with Python utilities
- Backtesting and analytics focus
- Ticket spread tracking and arbitrage detection
- Basic warehouse functionality
- Partial festival intelligence integration

**Key Components**:
- Backtesting system (TypeScript)
- Portfolio metrics (TypeScript)
- Ticket spread calculator (Python)
- Arbitrage alert tool (Python)
- Basic warehouse schema (DuckDB)
- Scraper adapters (TypeScript/Python)

### Intelligence Subtree (festival-intelligence)

**Current State**:
- Comprehensive Python-based festival intelligence system
- Multi-source scraping (MusicBrainz, Spotify, sentiment)
- Entity resolution and normalization
- Advanced warehouse with source lineage
- API and web application structure
- Extensive documentation

**Key Components**:
- Advanced entity resolution
- Multi-source ingestion pipeline
- Festival schema with source provenance
- FastAPI application
- Next.js web application
- Historical festival specifications
- Source registry and governance

---

## Component Classification Matrix

### KEEP - Components to preserve in consolidated codebase

#### Warehouse & Schema
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/warehouse/repository.py` | `python/festival_bloomberg/warehouse/repository.py` | Canonical warehouse access layer | intelligence | tests/test_repository.py | KEEP |
| `intelligence/warehouse/schema_loader.py` | `python/festival_bloomberg/warehouse/schema_loader.py` | Schema application logic | intelligence | tests/test_schema_duckdb.py | KEEP |
| `schema/duckdb.sql` | `schema/duckdb.sql` | Canonical DDL | main | tests/test_schema_duckdb.py | KEEP |

#### Entity Resolution
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/pipelines/entity_resolution.py` | `python/festival_bloomberg/entities/resolution.py` | Artist identity resolution | intelligence | scripts/test_entity_resolution.py | KEEP |
| `tests/fixtures/entity_resolution_fixtures.py` | `tests/fixtures/entity_resolution_fixtures.py` | Test fixtures for normalization | main | tests/test_entity_resolution_fixtures.py | KEEP |

#### Backtesting & Analytics
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `backtest_runner.ts` | `python/festival_bloomberg/backtesting/runner.py` | Historical backtesting system | main | tests/test_backtest.py | KEEP |
| `backtest/historical_backtest.py` | `python/festival_bloomberg/backtesting/models.py` | Backtesting data models | main | tests/test_arbitrage_integration.py | KEEP |
| `portfolio_metrics.ts` | `python/festival_bloomberg/analytics/portfolio.py` | Portfolio analytics | main | tests/test_portfolio.py | KEEP |
| `metrics/spread_calculator.py` | `python/festival_bloomberg/tickets/spread.py` | Ticket spread calculation | main | tests/test_spread.py | KEEP |

#### Source Registry & Governance
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/source_registry.yml` | `config/source_registry.yaml` | Source eligibility metadata | intelligence | None | KEEP |
| `python/festival_bloomberg/governance/source_registry.py` | `python/festival_bloomberg/governance/source_registry.py` | Source registry implementation | main | tests/test_source_registry.py | KEEP |

#### Scraping & Ingestion
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/pipelines/musicbrainz/` | `python/festival_bloomberg/sources/musicbrainz/` | MusicBrainz ingestion | intelligence | tests/test_musicbrainz.py | KEEP |
| `intelligence/scrapers/sentiment.py` | `python/festival_bloomberg/signals/sentiment.py` | Sentiment analysis | intelligence | tests/test_sentiment.py | KEEP |
| `src/scraper/musicbrainz.ts` | `python/festival_bloomberg/sources/musicbrainz/` | MusicBrainz client (merge) | main | tests/scraper/musicbrainz.test.ts | MERGE |

---

### MIGRATE - Components to move and adapt

#### API Layer
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/apps/api/` | `apps/api/` | FastAPI application | intelligence | tests/test_api.py | MIGRATE |
| `intelligence/warehouse/repository.py` | `apps/api/dependencies.py` | API dependencies | intelligence | tests/test_api.py | MERGE |

#### Web Application
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/apps/web/` | `apps/web/` | Next.js application | intelligence | tests/test_web.py | MIGRATE |

#### Historical Festival Data
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/warehouse/raw/festivals.parquet` | `data/historical/festivals.parquet` | Historical festival data | intelligence | tests/test_historical.py | MIGRATE |

---

### MERGE - Components to combine

#### MusicBrainz Integration
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `src/scraper/musicbrainz.ts` | `python/festival_bloomberg/sources/musicbrainz/client.py` | MusicBrainz API client | main | tests/scraper/musicbrainz.test.ts | MERGE |
| `intelligence/pipelines/musicbrainz/` | `python/festival_bloomberg/sources/musicbrainz/` | MusicBrainz ingestion | intelligence | tests/test_musicbrainz.py | MERGE |

#### Sentiment Analysis
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/scrapers/sentiment.py` | `python/festival_bloomberg/signals/sentiment.py` | Sentiment analysis | intelligence | tests/test_sentiment.py | MERGE |
| `src/scraper/sentiment.ts` | `python/festival_bloomberg/signals/sentiment.py` | Sentiment analysis (merge) | main | tests/scraper/sentiment.test.ts | MERGE |

---

### REWRITE - Components to rebuild with new architecture

#### Warehouse Interface
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `warehouse/repository.py` | `python/festival_bloomberg/warehouse/repository.py` | Unified warehouse interface | main | tests/test_repository.py | REWRITE |
| `intelligence/warehouse/repository.py` | (merged above) | Unified warehouse interface | intelligence | tests/test_repository.py | MERGE |

#### Point-in-Time Feature Store
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| (none) | `python/festival_bloomberg/features/pit_store.py` | Point-in-time feature storage | new | tests/test_pit_store.py | REWRITE |

#### Artist Factor Model
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| (none) | `python/festival_bloomberg/analytics/factors.py` | Artist factor calculation | new | tests/test_factors.py | REWRITE |

#### Expected Billing Model
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| (none) | `python/festival_bloomberg/models/billing.py` | Expected billing prediction | new | tests/test_billing_model.py | REWRITE |

---

### DEPRECATE - Components to phase out

#### Duplicate Schema Files
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/schema/duckdb.sql` | `schema/duckdb.sql` | Consolidated schema | intelligence | None | DEPRECATE |
| `database/festival_bloomberg_schema.py` | `schema/duckdb.sql` | Consolidated schema | main | None | DEPRECATE |

#### Legacy Scripts
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `scripts/test_*.py` | `tests/` | Test consolidation | intelligence | None | DEPRECATE |
| `run_backtest.js` | `python/festival_bloomberg/backtesting/cli.py` | Unified CLI | main | None | DEPRECATE |

#### Duplicate Utilities
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `python/utils/hetzner_llm.py` | `python/festival_bloomberg/llm/hetzner.py` | LLM integration | main | None | DEPRECATE |
| `intelligence/utils/` | `python/festival_bloomberg/utils/` | Unified utilities | intelligence | None | DEPRECATE |

---

### DELETE_AFTER_PARITY - Remove after functionality verified

#### Old Test Infrastructure
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/tests/` | `tests/` | Test consolidation | intelligence | None | DELETE_AFTER_PARITY |
| `tests/scraper/` | `tests/legacy_scraper/` | Legacy scraper tests | main | None | DELETE_AFTER_PARITY |

#### Documentation Duplication
| Old Path | New Path | Purpose | Source Repo | Tests | Status |
|---------|----------|---------|-------------|-------|--------|
| `intelligence/*.md` | `docs/` | Documentation consolidation | intelligence | None | DELETE_AFTER_PARITY |
| `intelligence/docs/` | `docs/` | Documentation consolidation | intelligence | None | DELETE_AFTER_PARITY |

---

## Dependency Graph Analysis

### Critical Dependencies

**Warehouse Layer**:
- `warehouse/repository.py` depends on: `duckdb`, schema DDL
- `intelligence/warehouse/repository.py` depends on: same
- **Action**: Merge into single canonical repository layer

**Entity Resolution**:
- `pipelines/entity_resolution.py` depends on: warehouse
- Scrapers depend on: entity resolution
- **Action**: Keep entity resolution, update to use canonical warehouse

**API Layer**:
- `intelligence/apps/api/` depends on: warehouse, entity resolution
- Main repo has no API layer
- **Action**: Migrate intelligence API to canonical location

**Backtesting**:
- Main repo backtesting depends on: warehouse (basic)
- Intelligence has no backtesting
- **Action**: Keep main repo backtesting, integrate with canonical warehouse

---

## Migration Status

### Phase 1: Foundation (Current)
- [x] Security hygiene completed
- [x] Research completed
- [x] Source registry implemented
- [x] Consolidation manifest created

### Phase 2: Canonical Warehouse
- [ ] Merge warehouse schemas into single DDL
- [ ] Create unified repository interface
- [ ] Migrate warehouse tests
- [ ] Update all imports to use canonical warehouse

### Phase 3: Entity Resolution
- [ ] Migrate entity resolution to canonical location
- [ ] Update test fixtures
- [ ] Integrate with canonical warehouse
- [ ] Remove old entity resolution code

### Phase 4: API Migration
- [ ] Migrate FastAPI application to apps/api/
- [ ] Update dependencies
- [ ] Migrate API tests
- [ ] Remove old API code

### Phase 5: Web Application
- [ ] Migrate Next.js application to apps/web/
- [ ] Update API integration
- [ ] Remove old web code

### Phase 6: Source Registry
- [ ] Consolidate source registry implementations
- [ ] Update all ingestion to use canonical registry
- [ ] Remove old registry code

### Phase 7: Backtesting Integration
- [ ] Integrate backtesting with canonical warehouse
- [ ] Update to use point-in-time data model
- [ ] Migrate backtesting tests

### Phase 8: Test Consolidation
- [ ] Consolidate all tests into tests/ directory
- [ ] Remove duplicate test files
- [ ] Ensure comprehensive test coverage

### Phase 9: Documentation
- [ ] Consolidate all documentation into docs/
- [ ] Remove duplicate documentation
- [ ] Update architecture documentation

### Phase 10: Cleanup
- [ ] Remove intelligence/ subtree
- [ ] Final verification
- [ ] Update README

---

## Architecture Target Structure

```
festival-bloomberg/
├── apps/
│   ├── api/                    # FastAPI application (migrated from intelligence)
│   └── web/                    # Next.js application (migrated from intelligence)
├── python/
│   └── festival_bloomberg/
│       ├── entities/          # Entity resolution (migrated from intelligence)
│       ├── features/          # Point-in-time feature store (new)
│       ├── sources/           # Data source adapters (consolidated)
│       ├── warehouse/         # Canonical warehouse (consolidated)
│       ├── analytics/         # Analytics and models (consolidated)
│       ├── backtesting/       # Backtesting system (from main)
│       ├── tickets/           # Ticket market analysis (from main)
│       ├── governance/        # Source registry (consolidated)
│       └── signals/           # Sentiment and attention (consolidated)
├── schema/
│   ├── duckdb.sql             # Canonical DDL (consolidated)
│   └── migrations/           # Migration scripts (new)
├── tests/
│   ├── unit/                  # Unit tests (consolidated)
│   ├── integration/           # Integration tests (consolidated)
│   └── fixtures/              # Test fixtures (consolidated)
├── config/
│   └── source_registry.yaml   # Source registry configuration
├── docs/
│   ├── ARCHITECTURE.md         # Architecture documentation
│   ├── CONSOLIDATION_MANIFEST.md # This document
│   └── DATA_GOVERNANCE.md     # Data governance documentation
└── data/                      # Ignored runtime data
```

---

## Risk Assessment

### High-Risk Areas
1. **Warehouse merge** - Critical data structure, requires careful testing
2. **Entity resolution integration** - Core functionality, test coverage essential
3. **API migration** - External interface, backward compatibility concerns
4. **Test consolidation** - Risk of losing test coverage

### Medium-Risk Areas
1. **Source registry consolidation** - Configuration changes, licensing concerns
2. **Backtesting integration** - Complex logic, depends on warehouse
3. **Web application migration** - User-facing, requires testing

### Low-Risk Areas
1. **Documentation consolidation** - Informational only
2. **Script consolidation** - Utility functions, can be rewritten
3. **Duplicate test removal** - After verification, safe to remove

---

## Success Criteria

### Functional Requirements
- [ ] All tests pass after consolidation
- [ ] API functionality preserved
- [ ] Web application functional
- [ ] Backtesting system operational
- [ ] Warehouse queries working
- [ ] Entity resolution accurate

### Quality Requirements
- [ ] No duplicate code
- [ ] Single source of truth for each component
- [ ] Comprehensive test coverage
- [ ] Updated documentation
- [ ] Clean git history

### Performance Requirements
- [ ] No performance regression
- [ ] API response times maintained
- [ ] Warehouse query performance acceptable

---

## Timeline Estimate

- **Phase 1 (Completed)**: Security, research, source registry - 2 days
- **Phase 2-3 (Foundation)**: Warehouse and entity resolution - 3-4 days
- **Phase 4-5 (Application)**: API and web migration - 2-3 days
- **Phase 6-7 (Integration)**: Source registry and backtesting - 2-3 days
- **Phase 8-10 (Cleanup)**: Tests, documentation, cleanup - 2-3 days

**Total Estimated**: 11-16 days

---

## Next Steps

1. **Immediate**: Begin Phase 2 - canonical warehouse consolidation
2. **Priority**: Focus on critical path components (warehouse, entity resolution)
3. **Testing**: Continuous testing after each phase
4. **Documentation**: Update architecture documentation as changes are made
5. **Risk Management**: Monitor high-risk areas closely, rollback plan available

---

**Manifest Created**: August 12, 2026  
**Next Review**: After Phase 2 completion  
**Owner**: Festival Bloomberg Engineering