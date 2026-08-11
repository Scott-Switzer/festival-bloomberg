"""
Festival Bloomberg Database Schema
Implements the comprehensive schema from the Festival Bloomberg specification
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, DateTime, Date, 
    Time, ForeignKey, JSON, Text, CheckConstraint, UniqueConstraint,
    Index
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import uuid

Base = declarative_base()


def generate_uuid():
    return str(uuid.uuid4())


# ============================================================================
# Identity, Source, and Lineage Tables
# ============================================================================

class SourceSystem(Base):
    """Source system registry for all data providers"""
    __tablename__ = 'source_system'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    provider_name = Column(String(255), nullable=False)
    source_type = Column(String(100), nullable=False)  # official_api, ticketing, streaming, social, crm, contract, public_web, analyst_input, file_upload
    base_url = Column(Text)
    api_version = Column(String(50))
    terms_url = Column(Text)
    refresh_cadence = Column(String(100))
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    source_records = relationship("SourceRecord", back_populates="source_system")
    external_id_maps = relationship("ExternalIdMap", back_populates="source_system")
    data_quality_issues = relationship("DataQualityIssue", back_populates="source_system")
    
    # Additional metadata
    meta_data = Column(JSON, nullable=True)


class SourceRecord(Base):
    """Raw source record tracking with payload references"""
    __tablename__ = 'source_record'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    source_system_id = Column(UUID(as_uuid=True), ForeignKey('source_system.id'), nullable=False)
    source_record_key = Column(Text, nullable=False)
    endpoint = Column(String(500))
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    observed_at = Column(DateTime(timezone=True))
    payload_uri = Column(Text, nullable=False)  # R2 object reference
    payload_hash = Column(String(64), nullable=False)  # SHA256
    http_status = Column(Integer)
    parser_version = Column(String(50))
    data_status = Column(String(50), nullable=False, default='observed')  # reported, observed, inferred, modeled, disputed, deprecated
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    source_system = relationship("SourceSystem", back_populates="source_records")
    data_quality_issues = relationship("DataQualityIssue", back_populates="source_record")
    
    __table_args__ = (
        UniqueConstraint('source_system_id', 'source_record_key', 'payload_hash', name='uq_source_record'),
    )


class ExternalIdMap(Base):
    """Cross-reference table for external provider IDs"""
    __tablename__ = 'external_id_map'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    entity_type = Column(String(100), nullable=False)  # artist, festival, venue, organization, person
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    source_system_id = Column(UUID(as_uuid=True), ForeignKey('source_system.id'), nullable=False)
    external_id = Column(Text, nullable=False)
    external_name = Column(String(500))
    match_method = Column(String(100))
    match_confidence = Column(Numeric(5, 4))
    first_seen_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    source_system = relationship("SourceSystem", back_populates="external_id_maps")
    
    __table_args__ = (
        UniqueConstraint('source_system_id', 'entity_type', 'external_id', name='uq_external_id'),
    )


class DataQualityIssue(Base):
    """Data quality tracking and quarantine"""
    __tablename__ = 'data_quality_issue'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    source_record_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    entity_type = Column(String(100))
    entity_id = Column(UUID(as_uuid=True))
    issue_type = Column(String(100), nullable=False)
    severity = Column(String(50), nullable=False)  # critical, high, medium, low
    field_name = Column(String(255))
    detail = Column(Text)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True))
    
    # Relationships
    source_record = relationship("SourceRecord", back_populates="data_quality_issues")
    source_system = relationship("SourceSystem", back_populates="data_quality_issues")


# ============================================================================
# Festival and Location Dimensions
# ============================================================================

class Venue(Base):
    """Venue dimension with location and capacity data"""
    __tablename__ = 'venue'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name = Column(String(500), nullable=False)
    operator_name = Column(String(500))
    address_line = Column(Text)
    city = Column(String(200))
    region = Column(String(200))
    country_code = Column(String(2))
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    capacity = Column(Integer)
    indoor_outdoor = Column(String(50))  # indoor, outdoor, mixed
    timezone = Column(String(100))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    festivals = relationship("Festival", back_populates="venue")


class Festival(Base):
    """Festival dimension with portfolio tracking"""
    __tablename__ = 'festival'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name = Column(String(500), nullable=False)
    organizer_name = Column(String(500))
    festival_type = Column(String(100))
    primary_genres = Column(ARRAY(String))
    city = Column(String(200))
    region = Column(String(200))
    country_code = Column(String(2))
    venue_id = Column(UUID(as_uuid=True), ForeignKey('venue.id'))
    website_url = Column(Text)
    audience_capacity = Column(Integer)
    brand_positioning = Column(Text)
    audience_demographic_summary = Column(JSON)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Festival Bloomberg additions
    parent_festival_id = Column(UUID(as_uuid=True))
    property_family = Column(String(200))  # Lollapalooza, ACL, etc.
    format_profile = Column(String(100))  # poster_grid, day_stage_schedule, multi_weekend, etc.
    default_country_code = Column(String(2))
    default_currency = Column(String(3))
    official_domain = Column(String(500))
    
    # Relationships
    venue = relationship("Venue", back_populates="festivals")
    editions = relationship("FestivalEdition", back_populates="festival")
    relationships = relationship("FestivalRelationship", back_populates="festival")


class FestivalEdition(Base):
    """Festival edition with dates and performance data"""
    __tablename__ = 'festival_edition'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_id = Column(UUID(as_uuid=True), ForeignKey('festival.id'), nullable=False)
    edition_year = Column(Integer, nullable=False)
    start_date = Column(Date)
    end_date = Column(Date)
    announced_at = Column(DateTime(timezone=True))
    actual_attendance = Column(Integer)
    capacity = Column(Integer)
    weather_summary = Column(JSON)
    operating_status = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Festival Bloomberg additions
    edition_label = Column(String(200))
    production_role = Column(String(100))  # producer, co_producer, presenter, promoter, owner, local_partner, historical_association
    local_presenter = Column(String(500))
    local_promoter = Column(String(500))
    venue_name = Column(String(500))
    venue_city = Column(String(200))
    venue_region = Column(String(200))
    country_code = Column(String(2))
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    currency = Column(String(3))
    format_profile = Column(String(100))
    weekend_count = Column(Integer, default=1)
    edition_status = Column(String(50))  # announced, active, completed, cancelled, paused
    source_coverage_scope = Column(String(100))  # complete, partial, unknown
    lineup_cutoff_ts = Column(DateTime(timezone=True))
    announcement_date_quality = Column(String(50))  # official, estimated, unknown
    
    # Relationships
    festival = relationship("Festival", back_populates="editions")
    stages = relationship("FestivalStage", back_populates="festival_edition")
    performances = relationship("ArtistFestivalPerformance", back_populates="festival_edition")
    occurrences = relationship("FestivalOccurrence", back_populates="festival_edition")
    lineup_revisions = relationship("LineupRevision", back_populates="festival_edition")
    
    __table_args__ = (
        UniqueConstraint('festival_id', 'edition_year', name='uq_festival_edition'),
    )


class FestivalStage(Base):
    """Stage dimension within festival editions"""
    __tablename__ = 'festival_stage'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    name = Column(String(500), nullable=False)
    stage_type = Column(String(100))
    capacity = Column(Integer)
    genres = Column(ARRAY(String))
    age_restriction = Column(String(100))
    start_time = Column(Time)
    end_time = Column(Time)
    production_specs = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Festival Bloomberg additions
    stage_name = Column(String(500))
    stage_type = Column(String(100))
    capacity_estimate = Column(Integer)
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    meta_data = Column(JSON)
    
    # Relationships
    festival_edition = relationship("FestivalEdition", back_populates="stages")


# ============================================================================
# Artist Analytics Tables
# ============================================================================

class Artist(Base):
    """Artist dimension with canonical identity"""
    __tablename__ = 'artist'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    canonical_name = Column(String(500), nullable=False)
    legal_name = Column(String(500))
    artist_type = Column(String(100))  # person, group, ensemble, unknown
    country_code = Column(String(2))
    formation_year = Column(Integer)
    active_status = Column(String(50))
    website_url = Column(Text)
    biography = Column(Text)
    current_genre_labels = Column(ARRAY(String))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Festival Bloomberg additions
    sort_name = Column(String(500))
    country_or_region = Column(ARRAY(String))
    formed_year = Column(Integer)
    disbanded_year = Column(Integer)
    wikipedia_title = Column(String(500))
    lastfm_mbid = Column(String(100))
    musicbrainz_id = Column(String(100))
    meta_data = Column(JSON)
    
    # Relationships
    metrics = relationship("ArtistMetricObservation", back_populates="artist")
    genre_classifications = relationship("ArtistGenreClassification", back_populates="artist")
    booking_quotes = relationship("ArtistBookingQuote", back_populates="artist")
    performances = relationship("ArtistFestivalPerformance", back_populates="artist")
    route_legs = relationship("ArtistRouteLeg", back_populates="artist")
    overlap_scores = relationship("ArtistOverlapScore", foreign_keys="[ArtistOverlapScore.artist_id_a]")
    overlap_scores_b = relationship("ArtistOverlapScore", foreign_keys="[ArtistOverlapScore.artist_id_b]")


class ArtistMetricObservation(Base):
    """Time-series artist metrics from various platforms"""
    __tablename__ = 'artist_metric_observation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    metric_date = Column(Date, nullable=False)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Numeric)
    metric_unit = Column(String(50))
    geography = Column(String(200))
    platform = Column(String(100))
    audience_segment = Column(String(100))
    rank_value = Column(Numeric)
    sample_size = Column(Integer)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    confidence = Column(String(50))  # high, medium, low, unknown
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist = relationship("Artist", back_populates="metrics")
    
    __table_args__ = (
        UniqueConstraint('artist_id', 'metric_date', 'metric_name', 'geography', 'platform', 'audience_segment', 'source_id', name='uq_artist_metric'),
    )


class ArtistGenreClassification(Base):
    """Genre classifications with probability scores"""
    __tablename__ = 'artist_genre_classification'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    genre_taxonomy = Column(String(100), nullable=False)
    genre_code = Column(String(100), nullable=False)
    genre_label = Column(String(200), nullable=False)
    probability = Column(Numeric(6, 5))
    primary_flag = Column(Boolean, nullable=False, default=False)
    classification_method = Column(String(100))
    observed_at = Column(DateTime(timezone=True), nullable=False)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist = relationship("Artist", back_populates="genre_classifications")


class ArtistBookingQuote(Base):
    """Artist booking fee ranges and terms"""
    __tablename__ = 'artist_booking_quote'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    quote_date = Column(Date, nullable=False)
    engagement_type = Column(String(100))
    territory = Column(String(200))
    performance_date_start = Column(Date)
    performance_date_end = Column(Date)
    low_amount = Column(Numeric(18, 2))
    expected_amount = Column(Numeric(18, 2))
    high_amount = Column(Numeric(18, 2))
    currency = Column(String(3), nullable=False)
    buyout_or_guarantee = Column(String(50))
    deposit_pct = Column(Numeric(6, 3))
    production_cost = Column(Numeric(18, 2))
    travel_cost = Column(Numeric(18, 2))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    confidence = Column(String(50))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist = relationship("Artist", back_populates="booking_quotes")


class ArtistFestivalPerformance(Base):
    """Historical festival performances"""
    __tablename__ = 'artist_festival_performance'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'))
    stage_id = Column(UUID(as_uuid=True), ForeignKey('festival_stage.id'))
    performance_date = Column(Date)
    set_start = Column(DateTime(timezone=True))
    set_end = Column(DateTime(timezone=True))
    billing_level = Column(String(100))
    attendance_estimate = Column(Integer)
    set_length_minutes = Column(Integer)
    performance_status = Column(String(50))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist = relationship("Artist", back_populates="performances")
    festival_edition = relationship("FestivalEdition", back_populates="performances")


class ArtistRouteLeg(Base):
    """Artist routing and tour logistics"""
    __tablename__ = 'artist_route_leg'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    event_date = Column(Date, nullable=False)
    city = Column(String(200))
    country_code = Column(String(2))
    venue_name = Column(String(500))
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    leg_sequence = Column(Integer)
    distance_km = Column(Numeric(12, 2))
    travel_mode = Column(String(100))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist = relationship("Artist", back_populates="route_legs")


class ArtistOverlapScore(Base):
    """Audience overlap metrics between artist pairs"""
    __tablename__ = 'artist_overlap_score'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id_a = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    artist_id_b = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    observation_date = Column(Date, nullable=False)
    geography = Column(String(200))
    overlap_metric = Column(String(100), nullable=False)
    score = Column(Numeric(10, 6))
    method_version = Column(String(100))
    sample_size = Column(Integer)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist_a = relationship("Artist", foreign_keys=[artist_id_a])
    artist_b = relationship("Artist", foreign_keys=[artist_id_b])
    
    __table_args__ = (
        CheckConstraint('artist_id_a <> artist_id_b', name='ck_artist_overlap_different'),
    )


# ============================================================================
# Management and Contacts Tables
# ============================================================================

class Organization(Base):
    """Organization dimension for agencies, labels, promoters"""
    __tablename__ = 'organization'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    legal_name = Column(String(500), nullable=False)
    trading_name = Column(String(500))
    organization_type = Column(String(100))
    website_url = Column(Text)
    headquarters_city = Column(String(200))
    headquarters_country_code = Column(String(2))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    representations = relationship("ArtistRepresentation", back_populates="organization")
    contacts = relationship("OrganizationContact", back_populates="organization")
    festival_relationships = relationship("FestivalRelationship", back_populates="organization")


class Person(Base):
    """Person dimension for individual contacts"""
    __tablename__ = 'person'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    display_name = Column(String(500), nullable=False)
    title = Column(String(200))
    email_encrypted = Column(Text)
    phone_encrypted = Column(Text)
    timezone = Column(String(100))
    preferred_contact_channel = Column(String(100))
    consent_status = Column(String(50))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization_contacts = relationship("OrganizationContact", back_populates="person")


class ArtistRepresentation(Base):
    """Artist representation relationships"""
    __tablename__ = 'artist_representation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organization.id'), nullable=False)
    representation_type = Column(String(100), nullable=False)  # booking, representation, unknown
    territory = Column(String(200))
    exclusive_flag = Column(Boolean)
    valid_from = Column(Date)
    valid_to = Column(Date)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    artist = relationship("Artist")
    organization = relationship("Organization", back_populates="representations")


class OrganizationContact(Base):
    """Organization contact mappings"""
    __tablename__ = 'organization_contact'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organization.id'), nullable=False)
    person_id = Column(UUID(as_uuid=True), ForeignKey('person.id'), nullable=False)
    role = Column(String(200))
    territory = Column(String(200))
    priority_rank = Column(Integer)
    valid_from = Column(Date)
    valid_to = Column(Date)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    organization = relationship("Organization", back_populates="contacts")
    person = relationship("Person", back_populates="organization_contacts")


class ContactInteraction(Base):
    """Contact interaction history"""
    __tablename__ = 'contact_interaction'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organization.id'))
    person_id = Column(UUID(as_uuid=True), ForeignKey('person.id'))
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'))
    interaction_at = Column(DateTime(timezone=True), nullable=False)
    channel = Column(String(100))
    direction = Column(String(50))
    subject = Column(String(500))
    outcome = Column(String(200))
    next_action_at = Column(DateTime(timezone=True))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


# ============================================================================
# Festival Matching and Fit Tables
# ============================================================================

class AudienceSegment(Base):
    """Audience segment definitions"""
    __tablename__ = 'audience_segment'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name = Column(String(200), nullable=False)
    taxonomy = Column(String(100), nullable=False)
    age_min = Column(Integer)
    age_max = Column(Integer)
    income_band = Column(String(100))
    geography = Column(String(200))
    interests = Column(ARRAY(String))
    weights = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class FestivalAudienceObservation(Base):
    """Festival audience demographics"""
    __tablename__ = 'festival_audience_observation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    audience_segment_id = Column(UUID(as_uuid=True), ForeignKey('audience_segment.id'), nullable=False)
    share = Column(Numeric(8, 5))
    count_estimate = Column(Integer)
    median_ticket_price = Column(Numeric(18, 2))
    currency = Column(String(3))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    observation_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ArtistAudienceObservation(Base):
    """Artist audience demographics"""
    __tablename__ = 'artist_audience_observation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    audience_segment_id = Column(UUID(as_uuid=True), ForeignKey('audience_segment.id'), nullable=False)
    share = Column(Numeric(8, 5))
    count_estimate = Column(Integer)
    geography = Column(String(200))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    observation_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class FestivalFitAssessment(Base):
    """Artist-festival fit scoring"""
    __tablename__ = 'festival_fit_assessment'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    stage_id = Column(UUID(as_uuid=True), ForeignKey('festival_stage.id'))
    assessment_date = Column(Date, nullable=False)
    demographic_score = Column(Numeric(8, 5))
    genre_score = Column(Numeric(8, 5))
    stage_score = Column(Numeric(8, 5))
    commercial_score = Column(Numeric(8, 5))
    routing_score = Column(Numeric(8, 5))
    strategic_score = Column(Numeric(8, 5))
    cannibalization_penalty = Column(Numeric(8, 5))
    total_score = Column(Numeric(8, 5))
    score_version = Column(String(50), nullable=False)
    rationale = Column(JSON)
    confidence = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class FestivalArtistCandidate(Base):
    """Artist candidate tracking for festivals"""
    __tablename__ = 'festival_artist_candidate'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    stage_id = Column(UUID(as_uuid=True), ForeignKey('festival_stage.id'))
    status = Column(String(50))
    target_fee = Column(Numeric(18, 2))
    currency = Column(String(3))
    target_set_length_minutes = Column(Integer)
    decision_owner = Column(String(200))
    decision_at = Column(DateTime(timezone=True))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# Financial Performance Projection Tables
# ============================================================================

class TicketTierObservation(Base):
    """Ticket tier pricing and inventory tracking"""
    __tablename__ = 'ticket_tier_observation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    tier_name = Column(String(200), nullable=False)
    tier_sequence = Column(Integer)
    price = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    quantity_available = Column(Integer)
    quantity_sold = Column(Integer)
    quantity_remaining = Column(Integer)
    on_sale_at = Column(DateTime(timezone=True))
    sell_through_at = Column(DateTime(timezone=True))
    observed_at = Column(DateTime(timezone=True), nullable=False)
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class HistoricalFestivalFinancial(Base):
    """Historical festival financial data"""
    __tablename__ = 'historical_festival_financial'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    metric_name = Column(String(200), nullable=False)
    metric_value = Column(Numeric(20, 4))
    metric_unit = Column(String(100))
    currency = Column(String(3))
    period_start = Column(Date)
    period_end = Column(Date)
    reported_or_modeled = Column(String(50), nullable=False)  # reported, modeled
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    confidence = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Sponsor(Base):
    """Sponsor dimension"""
    __tablename__ = 'sponsor'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name = Column(String(500), nullable=False)
    industry = Column(String(200))
    target_segments = Column(ARRAY(String))
    website_url = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class SponsorActivation(Base):
    """Sponsor activation tracking"""
    __tablename__ = 'sponsor_activation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    sponsor_id = Column(UUID(as_uuid=True), ForeignKey('sponsor.id'), nullable=False)
    activation_name = Column(String(500))
    contract_value = Column(Numeric(18, 2))
    currency = Column(String(3))
    activation_cost = Column(Numeric(18, 2))
    impressions = Column(Integer)
    engagements = Column(Integer)
    leads = Column(Integer)
    conversions = Column(Integer)
    attributed_revenue = Column(Numeric(18, 2))
    measurement_method = Column(String(200))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class VendorObservation(Base):
    """Vendor performance tracking"""
    __tablename__ = 'vendor_observation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    vendor_name = Column(String(500))
    vendor_category = Column(String(200))
    observation_time = Column(DateTime(timezone=True), nullable=False)
    transaction_count = Column(Integer)
    gross_sales = Column(Numeric(18, 2))
    average_order_value = Column(Numeric(18, 2))
    queue_minutes = Column(Numeric(10, 2))
    inventory_units_sold = Column(Integer)
    capacity_or_location = Column(String(200))
    source_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class FinancialProjection(Base):
    """Financial projection scenarios"""
    __tablename__ = 'financial_projection'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    projection_date = Column(Date, nullable=False)
    scenario = Column(String(100), nullable=False)  # downside, base, upside
    model_version = Column(String(100), nullable=False)
    attendance = Column(Integer)
    ticket_revenue = Column(Numeric(20, 2))
    sponsorship_revenue = Column(Numeric(20, 2))
    vendor_revenue = Column(Numeric(20, 2))
    other_revenue = Column(Numeric(20, 2))
    artist_cost = Column(Numeric(20, 2))
    production_cost = Column(Numeric(20, 2))
    marketing_cost = Column(Numeric(20, 2))
    operations_cost = Column(Numeric(20, 2))
    other_cost = Column(Numeric(20, 2))
    gross_profit = Column(Numeric(20, 2))
    contribution_margin = Column(Numeric(10, 5))
    cash_break_even_attendance = Column(Integer)
    currency = Column(String(3))
    assumptions = Column(JSON)
    lower_bound = Column(JSON)
    upper_bound = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


# ============================================================================
# Festival Bloomberg Additional Tables
# ============================================================================

class FestivalRelationship(Base):
    """Festival organization relationships"""
    __tablename__ = 'festival_relationship'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_id = Column(UUID(as_uuid=True), ForeignKey('festival.id'), nullable=False)
    organization_name = Column(String(500), nullable=False)
    role = Column(String(100), nullable=False)
    valid_from = Column(Date)
    valid_to = Column(Date)
    edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'))
    source_document_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    confidence = Column(Numeric(5, 4), nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    festival = relationship("Festival", back_populates="relationships")


class FestivalOccurrence(Base):
    """Festival occurrence tracking (multi-weekend, etc.)"""
    __tablename__ = 'festival_occurrence'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    weekend_number = Column(Integer)
    occurrence_label = Column(String(200))
    event_date = Column(Date)
    local_timezone = Column(String(100), nullable=False)
    venue_name = Column(String(500))
    venue_area = Column(String(200))
    stage_count = Column(Integer)
    ticketed = Column(Boolean)
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    weather_cancelled = Column(Boolean, default=False)
    meta_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    festival_edition = relationship("FestivalEdition", back_populates="occurrences")


class LineupRevision(Base):
    """Lineup revision tracking"""
    __tablename__ = 'lineup_revision'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'), nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'), nullable=False)
    revision_observed_at = Column(DateTime(timezone=True), nullable=False)
    revision_type = Column(String(100))
    coverage_scope = Column(String(100))
    added_count = Column(Integer)
    removed_count = Column(Integer)
    changed_count = Column(Integer)
    diff = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    festival_edition = relationship("FestivalEdition", back_populates="lineup_revisions")


class PortfolioEvidence(Base):
    """Portfolio relationship evidence tracking"""
    __tablename__ = 'portfolio_evidence'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    festival_id = Column(UUID(as_uuid=True), ForeignKey('festival.id'), nullable=False)
    edition_id = Column(UUID(as_uuid=True), ForeignKey('festival_edition.id'))
    claim_type = Column(String(100), nullable=False)
    claim_value = Column(String(500))
    source_document_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'), nullable=False)
    extracted_text = Column(Text)
    confidence = Column(Numeric(5, 4), nullable=False)
    reviewed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ArtistGenreObservation(Base):
    """Artist genre observations from various sources"""
    __tablename__ = 'artist_genre_observation'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artist.id'), nullable=False)
    source_system = Column(String(100), nullable=False)
    genre = Column(String(200), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    confidence = Column(Numeric(5, 4))
    source_document_id = Column(UUID(as_uuid=True), ForeignKey('source_record.id'))
    raw = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
