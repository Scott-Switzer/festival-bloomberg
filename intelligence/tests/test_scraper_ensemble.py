"""Offline tests for the scraper ensemble + sentiment analyzer.

Network is mocked so these run without external access. We stub
``requests.Session.get`` to return canned JSON/XML for each source, then assert
the orchestrator fuses them into a correct ArtistInsight.
"""
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, ".")

from scrapers.contracts import ArtistInsight, ScrapeResult, ScrapeStatus, SourceType
from scrapers.sentiment import SentimentAnalyzer
from scrapers.ensemble import ScraperEnsemble, _era_proxy


# --------------------------------------------------------------------------- #
# SentimentAnalyzer (offline, VADER is local)
# --------------------------------------------------------------------------- #
def test_vader_sentiment_positive():
    a = SentimentAnalyzer()
    assert a.available
    sb = a.analyze(["I love this album, it is amazing!", "Best concert ever, so happy"])
    assert sb.compound > 0.3
    assert sb.positive > 0.5
    assert a.label(sb.compound, sb.negative) == "positive"


def test_vader_sentiment_negative():
    a = SentimentAnalyzer()
    sb = a.analyze(["This was terrible and disappointing", "Worst show, awful experience"])
    assert sb.compound < -0.3
    assert sb.negative > 0.5
    assert a.label(sb.compound, sb.negative) == "negative"


def test_vader_topics_extracted():
    a = SentimentAnalyzer()
    topics = a.extract_topics([
        "They announced a new tour and album release",
        "Classic 90s comeback with nostalgia",
    ])
    assert "tour" in topics
    assert "new music" in topics
    assert "nostalgia" in topics


def test_sentiment_empty_input():
    a = SentimentAnalyzer()
    sb = a.analyze([])
    assert sb.sample_size == 0
    assert sb.compound == 0.0


# --------------------------------------------------------------------------- #
# Ensemble fusion with mocked network
# --------------------------------------------------------------------------- #
def _fake_session(responses: dict):
    """Build a fake requests.Session whose .get returns canned responses."""
    session = MagicMock()
    def _get(url, params=None, headers=None, timeout=None):
        for key, payload in responses.items():
            if key in url:
                r = MagicMock()
                r.status_code = 200
                r.ok = True
                if isinstance(payload, dict):
                    r.json.return_value = payload
                else:
                    r.text = payload
                    r.content = payload.encode("utf-8")
                return r
        # default empty
        r = MagicMock()
        r.status_code = 200
        r.ok = True
        r.json.return_value = {}
        r.text = ""
        r.content = b""
        return r
    session.get.side_effect = _get
    return session


def test_ensemble_fuses_mocked_sources():
    responses = {
        "musicbrainz.org": {
            "artists": [{"id": "mb-123", "type": "Group", "country": "GB",
                         "life-span": {"begin": "1985"}, "tags": [{"name": "alternative rock"}, {"name": "electronic"}]}]
        },
        "wikidata.org": {
            "entities": {"Q1": {
                "claims": {
                    "P17": [{"mainsnak": {"datavalue": {"value": {"id": "Q145"}}}}],
                    "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1985-01-01T00:00:00Z"}}}}],
                    "P136": [{"mainsnak": {"datavalue": {"value": {"id": "Q1131"}}}}],
                }
            }},
            "success": 1,
        },
        "wbgetentities": {
            "entities": {
                "Q145": {"labels": {"en": {"value": "United Kingdom"}}},
                "Q1131": {"labels": {"en": {"value": "alternative rock"}}},
            }
        },
        "hn.algolia.com": {
            "hits": [
                {"title": "Radiohead announces amazing new tour", "points": 500, "num_comments": 200},
                {"title": "I love this album, it is fantastic", "points": 300, "num_comments": 50},
            ]
        },
        "gdeltproject.org": {"articles": [{"title": "Radiohead tops charts again", "domain": "example.com"}]},
        "pitchfork.com/rss": "<rss><channel><item><title>Radiohead release review</title><description>great new record</description></item></channel></rss>",
        "wikipedia": {"extract": "Radiohead is an English rock band.", "description": "English rock band"},
    }
    session = _fake_session(responses)
    ens = ScraperEnsemble(use_llm=False)
    ins: ArtistInsight = ens.analyze_artist("Radiohead", session)

    assert ins.musicbrainz_id == "mb-123"
    assert ins.origin_country == "GB"  # MusicBrainz is the primary country source
    assert ins.active_since == 1985
    assert ins.era == "gen-x"  # 1985 falls in the gen-x band (1980-1994)
    assert "alternative rock" in ins.genres  # resolved from Wikidata Q-ID Q1131
    assert ins.mention_volume >= 3
    assert ins.sentiment.sample_size > 0
    assert "wikipedia" in ins.sources_used and "hackernews" in ins.sources_used
    # lineup fit
    ins2 = ens.lineup_fit(ins, festival_genres=["alternative rock", "indie"], festival_era_mix=["millennial"])
    assert ins2.lineup_fit_score is not None
    assert ins2.lineup_fit_score > 50  # genre + era overlap


def test_era_proxy():
    assert _era_proxy(1975) == "boomer/older"
    assert _era_proxy(1990) == "gen-x"
    assert _era_proxy(2000) == "millennial"
    assert _era_proxy(2018) == "gen-z / newest"
    assert _era_proxy(None) is None


def test_ensemble_handles_source_failure():
    """A crashing source must not break the ensemble."""
    session = MagicMock()
    session.get.side_effect = Exception("network down")
    ens = ScraperEnsemble(use_llm=False)
    ins = ens.analyze_artist("Anyone", session)
    # Still returns an insight object; most sources failed gracefully.
    assert isinstance(ins, ArtistInsight)
    assert ins.mention_volume == 0
