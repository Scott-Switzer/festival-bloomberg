"""
Database models and schema for Festival Intelligence Terminal.
Flexible, extensible schema design using SQLAlchemy ORM.
"""
from datetime import datetime
from typing import Optional, List
from enum import Enum

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime, 
    Text, ForeignKey, JSON, Index, UniqueConstraint, CheckConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid


Base = declarative_base()


class Artist(Base):
    """Artist information and metrics."""
    __tablename__ = 'artists'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    musicbrainz_id = Column(String(36), unique=True, nullable=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    normalized_name = Column(String(255), nullable=False, index=True)
    genres = Column(JSONB, default=list)  # List of genre strings
    origin_country = Column(String(2), nullable=True)  # ISO country code
    origin_city = Column(String(255), nullable=True)
    career_stage = Column(String(50), nullable=True)  # emerging, established, legendary
    active_years_start = Column(Integer, nullable=True)
    active_years_end = Column(Integer, nullable=True)
    
    # Streaming metrics (latest)
    monthly_listeners = Column(Integer, nullable=True)
    spotify_followers = Column(Integer, nullable=True)
    apple_music_followers = Column(Integer, nullable=True)
    
    # Social metrics (latest)
    instagram_followers = Column(Integer, nullable=True)
    twitter_followers = Column(Integer, nullable=True)
    tiktok_followers = Column(Integer, nullable=True)
    
    # Predictive metrics
    momentum_score = Column(Float, nullable=True)
    booking_value_index = Column(Float, nullable=True)
    breakthrough_probability = Column(Float, nullable=True)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    streaming_history = relationship("StreamingHistory", back_populates="artist")
    social_history = relationship("SocialHistory", back_populates="artist")
    festival_appearances = relationship("FestivalAppearance", back_populates="artist")
    contacts = relationship("Contact", back_populates="artist")
    
    __table_args__ = (
        Index('idx_artists_name_normalized', 'normalized_name'),
        Index('idx_artists_momentum', 'momentum_score'),
        Index('idx_artists_genres', 'genres', postgresql_using='gin'),
    )


class Festival(Base):
    """Festival information and characteristics."""
    __tablename__ = 'festivals'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    normalized_name = Column(String(255), nullable=False, index=True)
    location_country = Column(String(2), nullable=True)
    location_city = Column(String(255), nullable=True)
    location_region = Column(String(255), nullable=True)
    
    # Festival characteristics
    capacity = Column(Integer, nullable=True)
    genre_focus = Column(JSONB, default=list)  # List of genre strings
    festival_type = Column(String(50), nullable=True)  # music, arts, mixed
    venue_type = Column(String(50), nullable=True)  # outdoor, indoor, mixed
    duration_days = Column(Integer, nullable=True)
    
    # Typical dates
    typical_month = Column(Integer, nullable=True)  # 1-12
    typical_year_start = Column(Integer, nullable=True)
    typical_year_end = Column(Integer, nullable=True)
    
    # Pricing
    ticket_price_min = Column(Float, nullable=True)
    ticket_price_max = Column(Float, nullable=True)
    
    # Metrics
    prestige_score = Column(Float, nullable=True)
    average_attendance = Column(Integer, nullable=True)
    average_revenue = Column(Float, nullable=True)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    appearances = relationship("FestivalAppearance", back_populates="festival")
    lineups = relationship("FestivalLineup", back_populates="festival")
    
    __table_args__ = (
        Index('idx_festivals_name_normalized', 'normalized_name'),
        Index('idx_festivals_location', 'location_country', 'location_city'),
        Index('idx_festivals_genre', 'genre_focus', postgresql_using='gin'),
    )


class StreamingHistory(Base):
    """Historical streaming data for artists."""
    __tablename__ = 'streaming_history'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artists.id'), nullable=False, index=True)
    platform = Column(String(50), nullable=False)  # spotify, apple_music, etc.
    metric_type = Column(String(50), nullable=False)  # monthly_listeners, followers, streams
    value = Column(Integer, nullable=False)
    date = Column(DateTime, nullable=False, index=True)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    artist = relationship("Artist", back_populates="streaming_history")
    
    __table_args__ = (
        Index('idx_streaming_artist_date', 'artist_id', 'date'),
        Index('idx_streaming_platform_date', 'platform', 'date'),
    )


class SocialHistory(Base):
    """Historical social media data for artists."""
    __tablename__ = 'social_history'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artists.id'), nullable=False, index=True)
    platform = Column(String(50), nullable=False)  # instagram, twitter, tiktok
    metric_type = Column(String(50), nullable=False)  # followers, engagement, posts
    value = Column(Integer, nullable=False)
    date = Column(DateTime, nullable=False, index=True)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    artist = relationship("Artist", back_populates="social_history")
    
    __table_args__ = (
        Index('idx_social_artist_date', 'artist_id', 'date'),
        Index('idx_social_platform_date', 'platform', 'date'),
    )


class FestivalAppearance(Base):
    """Artist appearances at festivals."""
    __tablename__ = 'festival_appearances'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artists.id'), nullable=False, index=True)
    festival_id = Column(UUID(as_uuid=True), ForeignKey('festivals.id'), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    position = Column(String(50), nullable=True)  # headliner, sub-headliner, supporting
    stage = Column(String(255), nullable=True)
    day = Column(String(50), nullable=True)
    
    # Performance metrics
    attendance = Column(Integer, nullable=True)
    performance_score = Column(Float, nullable=True)  # 0-1 scale
    audience_response = Column(String(50), nullable=True)  # excellent, good, average, poor
    
    # Context
    weather_conditions = Column(String(100), nullable=True)
    ticket_price = Column(Float, nullable=True)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    artist = relationship("Artist", back_populates="festival_appearances")
    festival = relationship("Festival", back_populates="appearances")
    
    __table_args__ = (
        UniqueConstraint('artist_id', 'festival_id', 'year', name='unique_appearance'),
        Index('idx_appearances_year', 'year'),
        Index('idx_appearances_performance', 'performance_score'),
    )


class FestivalLineup(Base):
    """Complete festival lineups by year."""
    __tablename__ = 'festival_lineups'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    festival_id = Column(UUID(as_uuid=True), ForeignKey('festivals.id'), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    
    # Lineup data
    artists = Column(JSONB, nullable=False)  # List of artist IDs with positions
    genre_distribution = Column(JSONB, default=dict)  # Genre counts
    headliner_count = Column(Integer, nullable=True)
    total_artists = Column(Integer, nullable=True)
    
    # Performance
    actual_attendance = Column(Integer, nullable=True)
    actual_revenue = Column(Float, nullable=True)
    sell_out_days = Column(Integer, nullable=True)
    success_score = Column(Float, nullable=True)  # 0-1 scale
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    festival = relationship("Festival", back_populates="lineups")
    
    __table_args__ = (
        UniqueConstraint('festival_id', 'year', name='unique_lineup'),
        Index('idx_lineups_year', 'year'),
        Index('idx_lineups_success', 'success_score'),
    )


class Contact(Base):
    """Industry contact information."""
    __tablename__ = 'contacts'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    role = Column(String(100), nullable=False)  # talent_buyer, booking_agent, manager, etc.
    company = Column(String(255), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(50), nullable=True)
    
    # Artist association (if contact represents artist)
    artist_id = Column(UUID(as_uuid=True), ForeignKey('artists.id'), nullable=True, index=True)
    
    # Verification
    verification_status = Column(String(50), default='unverified')  # unverified, verified, outdated
    verification_date = Column(DateTime, nullable=True)
    
    # Social media
    social_media = Column(JSONB, default=dict)  # platform handles
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    artist = relationship("Artist", back_populates="contacts")
    
    __table_args__ = (
        Index('idx_contacts_role', 'role'),
        Index('idx_contacts_company', 'company'),
    )


class User(Base):
    """User accounts (for future authentication)."""
    __tablename__ = 'users'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    role = Column(String(50), default='user')  # user, admin, etc.
    
    # Authentication (for future use)
    password_hash = Column(String(255), nullable=True)
    
    # Verification
    verification_status = Column(String(50), default='unverified')
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Message(Base):
    """Messages for communication platform."""
    __tablename__ = 'messages'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, index=True)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True, index=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey('groups.id'), nullable=True, index=True)
    
    content = Column(Text, nullable=False)
    message_type = Column(String(50), default='direct')  # direct, group, broadcast
    priority = Column(String(50), default='normal')  # normal, high, urgent
    
    # Status
    read_at = Column(DateTime, nullable=True)
    
    # Attachments
    attachments = Column(JSONB, default=list)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Group(Base):
    """Group chats for communication platform."""
    __tablename__ = 'groups'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    creator_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    topic = Column(String(500), nullable=True)
    is_private = Column(Boolean, default=True)
    
    # Participants (stored as JSON for MVP, separate table for production)
    participants = Column(JSONB, default=list)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class NewsItem(Base):
    """News items for intelligence feed."""
    __tablename__ = 'news_items'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)  # artist_news, festival_news, etc.
    url = Column(String(1000), nullable=True)
    
    # Analysis
    importance = Column(Float, nullable=True)  # 0-1 scale
    sentiment = Column(String(50), nullable=True)  # positive, negative, neutral
    entities = Column(JSONB, default=list)  # Extracted entities
    
    # Relevance
    relevance_score = Column(Float, nullable=True)
    
    # Metadata
    published_at = Column(DateTime, nullable=False, index=True)
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_news_category', 'category'),
        Index('idx_news_published', 'published_at'),
        Index('idx_news_importance', 'importance'),
    )


class Prediction(Base):
    """Stored predictions from analytics engine."""
    __tablename__ = 'predictions'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prediction_type = Column(String(50), nullable=False)  # breakthrough, lineup_success, etc.
    entity_type = Column(String(50), nullable=False)  # artist, festival
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Prediction data
    prediction = Column(JSONB, nullable=False)
    confidence = Column(Float, nullable=True)
    confidence_interval = Column(JSONB, default=list)  # [lower, upper]
    
    # Context
    model_version = Column(String(50), nullable=True)
    features_used = Column(JSONB, default=list)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_predictions_entity', 'entity_type', 'entity_id'),
        Index('idx_predictions_type', 'prediction_type'),
        Index('idx_predictions_created', 'created_at'),
    )


class DataQualityLog(Base):
    """Data quality validation logs."""
    __tablename__ = 'data_quality_logs'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_type = Column(String(50), nullable=False)  # artist, festival, etc.
    data_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    
    # Validation results
    quality_score = Column(Float, nullable=False)
    is_valid = Column(Boolean, nullable=False)
    errors = Column(JSONB, default=list)
    warnings = Column(JSONB, default=list)
    anomalies = Column(JSONB, default=list)
    
    # Metadata
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_quality_type', 'data_type'),
        Index('idx_quality_score', 'quality_score'),
        Index('idx_quality_created', 'created_at'),
    )


def get_database_url(config):
    """Generate database URL from configuration."""
    return f"postgresql://{config.username}:{config.password}@{config.host}:{config.port}/{config.database}"


def create_engine_from_config(config):
    """Create SQLAlchemy engine from configuration."""
    database_url = get_database_url(config)
    return create_engine(
        database_url,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_pre_ping=True,
        echo=False
    )


def create_tables(engine):
    """Create all tables in the database."""
    Base.metadata.create_all(engine)


def drop_tables(engine):
    """Drop all tables from the database."""
    Base.metadata.drop_all(engine)
