-- Migration: Fix duplicate core.festival_ticket_tiers table definition
-- This migration resolves the schema issue where festival_ticket_tiers was defined twice
-- with incompatible schemas. The ticket spread tracker uses the secondary market definition.

-- Check if the old table exists and has the old schema structure
DO $$
BEGIN
    -- Check if the old table structure exists (has tier_key instead of id)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'festival_ticket_tiers' 
        AND column_name = 'tier_key'
    ) THEN
        -- Drop the old table and its indexes
        DROP TABLE IF EXISTS core.festival_ticket_tiers CASCADE;
        
        -- Create the canonical table using the ticket secondary market schema
        CREATE TABLE core.festival_ticket_tiers (
            id VARCHAR PRIMARY KEY,
            edition_id VARCHAR NOT NULL,
            tier_name VARCHAR NOT NULL,
            tier_rank INTEGER NOT NULL,
            tier_type VARCHAR NOT NULL,
            access_scope VARCHAR NOT NULL,
            face_value_minor BIGINT,
            currency VARCHAR(3) NOT NULL,
            fee_components_minor BIGINT,
            total_primary_price_minor BIGINT,
            is_sold_out BOOLEAN DEFAULT FALSE,
            url VARCHAR,
            created_at TIMESTAMP NOT NULL
        );
        
        RAISE NOTICE 'Migrated from old festival_ticket_tiers schema to canonical schema';
    ELSE
        -- Table either doesn't exist or already has the correct schema
        -- Ensure the canonical schema exists
        DROP TABLE IF EXISTS core.festival_ticket_tiers CASCADE;
        
        CREATE TABLE core.festival_ticket_tiers (
            id VARCHAR PRIMARY KEY,
            edition_id VARCHAR NOT NULL,
            tier_name VARCHAR NOT NULL,
            tier_rank INTEGER NOT NULL,
            tier_type VARCHAR NOT NULL,
            access_scope VARCHAR NOT NULL,
            face_value_minor BIGINT,
            currency VARCHAR(3) NOT NULL,
            fee_components_minor BIGINT,
            total_primary_price_minor BIGINT,
            is_sold_out BOOLEAN DEFAULT FALSE,
            url VARCHAR,
            created_at TIMESTAMP NOT NULL
        );
        
        RAISE NOTICE 'Ensured canonical festival_ticket_tiers schema exists';
    END IF;
END $$;

-- Create indexes for the canonical table
CREATE INDEX IF NOT EXISTS idx_ticket_tiers_edition 
    ON core.festival_ticket_tiers (edition_id);
CREATE INDEX IF NOT EXISTS idx_ticket_tiers_tier_rank 
    ON core.festival_ticket_tiers (tier_rank);