#!/usr/bin/env python3
"""
Arbitrage Alert CLI Tool

Scans for secondary-market ticket prices exceeding primary tiers by over 15%
and prints an actionable booking arbitrage summary.
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import duckdb
from warehouse.repository import FestivalRepository, TicketRepository


def scan_arbitrage_opportunities(
    db_path: str,
    spread_threshold: float = 15.0,
    min_spread_minor: int = 1000  # $10.00 minimum absolute spread
) -> List[Dict[str, Any]]:
    """
    Scan for arbitrage opportunities where secondary market exceeds primary by threshold.
    
    Args:
        db_path: Path to DuckDB warehouse
        spread_threshold: Minimum percentage spread (default 15%)
        min_spread_minor: Minimum absolute spread in minor units (default $10.00)
    
    Returns:
        List of arbitrage opportunity records
    """
    conn = duckdb.connect(db_path)
    
    query = """
    SELECT 
        s.id as observation_id,
        s.edition_id,
        s.source as secondary_source,
        s.listing_url,
        s.title,
        s.ticket_type,
        s.section,
        s."row",
        s.quantity,
        s.price_minor as secondary_price_minor,
        s.currency as secondary_currency,
        s.total_buyer_price_minor,
        s.is_active,
        s.retrieved_at,
        t.id as tier_id,
        t.tier_name,
        t.tier_type,
        t.access_scope,
        t.face_value_minor as primary_price_minor,
        t.currency as primary_currency,
        t.total_primary_price_minor,
        t.is_sold_out,
        sp.absolute_spread_minor,
        sp.percentage_spread,
        sp.buyer_margin,
        sp.arbitrage_candidate,
        sp.calculated_at
    FROM core.secondary_ticket_observations s
    JOIN metrics.ticket_price_spreads sp ON s.id = sp.secondary_observation_id
    JOIN core.festival_ticket_tiers t ON sp.primary_tier_id = t.id
    WHERE sp.percentage_spread > ?
      AND sp.absolute_spread_minor > ?
      AND s.is_active = TRUE
      AND sp.arbitrage_candidate = TRUE
      AND sp.quality_flags NOT LIKE '%MISSING%'
    ORDER BY sp.percentage_spread DESC
    """
    
    try:
        result = conn.execute(query, [spread_threshold, min_spread_minor])
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        
        opportunities = []
        for row in rows:
            opportunity = dict(zip(columns, row))
            # Add calculated fields for display
            if opportunity.get('primary_price_minor'):
                opportunity['primary_price_usd'] = opportunity['primary_price_minor'] / 100.0
            if opportunity.get('secondary_price_minor'):
                opportunity['secondary_price_usd'] = opportunity['secondary_price_minor'] / 100.0
            if opportunity.get('absolute_spread_minor'):
                opportunity['absolute_spread_usd'] = opportunity['absolute_spread_minor'] / 100.0
            opportunities.append(opportunity)
        
        return opportunities
    finally:
        conn.close()


def format_arbitrage_summary(opportunities: List[Dict[str, Any]]) -> str:
    """
    Format arbitrage opportunities into actionable summary.
    
    Args:
        opportunities: List of arbitrage opportunity records
    
    Returns:
        Formatted summary string
    """
    if not opportunities:
        return "No arbitrage opportunities found meeting criteria."
    
    lines = []
    lines.append("=" * 80)
    lines.append("TICKET ARBITRAGE ALERT SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.utcnow().isoformat()}")
    lines.append(f"Total Opportunities: {len(opportunities)}")
    lines.append("")
    
    # Group by edition
    by_edition: Dict[str, List[Dict[str, Any]]] = {}
    for opp in opportunities:
        edition_id = opp.get('edition_id', 'UNKNOWN')
        if edition_id not in by_edition:
            by_edition[edition_id] = []
        by_edition[edition_id].append(opp)
    
    for edition_id, edition_opps in by_edition.items():
        lines.append(f"EDITION: {edition_id}")
        lines.append(f"  Opportunities: {len(edition_opps)}")
        lines.append("")
        
        for i, opp in enumerate(edition_opps[:10], 1):  # Top 10 per edition
            lines.append(f"  [{i}] {opp.get('title', 'Unknown Listing')}")
            lines.append(f"      Tier: {opp.get('tier_name', 'N/A')} ({opp.get('tier_type', 'N/A')})")
            lines.append(f"      Primary Price: ${opp.get('primary_price_usd', 0):.2f}")
            lines.append(f"      Secondary Price: ${opp.get('secondary_price_usd', 0):.2f}")
            lines.append(f"      Spread: ${opp.get('absolute_spread_usd', 0):.2f} ({opp.get('percentage_spread', 0):.2f}%)")
            lines.append(f"      Source: {opp.get('secondary_source', 'N/A')}")
            lines.append(f"      URL: {opp.get('listing_url', 'N/A')}")
            lines.append(f"      Section: {opp.get('section', 'N/A')}, Row: {opp.get('row', 'N/A')}")
            lines.append(f"      Quantity: {opp.get('quantity', 'N/A')}")
            lines.append("")
        
        if len(edition_opps) > 10:
            lines.append(f"  ... and {len(edition_opps) - 10} more opportunities")
            lines.append("")
    
    # Summary statistics
    lines.append("=" * 80)
    lines.append("SUMMARY STATISTICS")
    lines.append("=" * 80)
    
    total_spread = sum(o.get('absolute_spread_minor', 0) for o in opportunities) / 100.0
    avg_spread = sum(o.get('percentage_spread', 0) for o in opportunities) / len(opportunities)
    max_spread = max(o.get('percentage_spread', 0) for o in opportunities)
    
    lines.append(f"Total Potential Arbitrage Value: ${total_spread:,.2f}")
    lines.append(f"Average Spread: {avg_spread:.2f}%")
    lines.append(f"Highest Spread: {max_spread:.2f}%")
    lines.append("")
    
    # Action recommendations
    lines.append("=" * 80)
    lines.append("ACTION RECOMMENDATIONS")
    lines.append("=" * 80)
    lines.append("1. Review high-spread listings for pricing errors or fraud")
    lines.append("2. Consider adjusting primary tier pricing for competitive positioning")
    lines.append("3. Monitor secondary market for demand signals")
    lines.append("4. Investigate sold-out tiers for potential additional inventory")
    lines.append("")
    
    return "\n".join(lines)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scan for ticket arbitrage opportunities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m python.tools.arbitrage_alert
  python -m python.tools.arbitrage_alert --spread-threshold 20.0
  python -m python.tools.arbitrage_alert --db-path custom_warehouse.duckdb
        """
    )
    
    parser.add_argument(
        '--db-path',
        default='data/warehouse/festival_bloomberg.duckdb',
        help='Path to DuckDB warehouse (default: data/warehouse/festival_bloomberg.duckdb)'
    )
    
    parser.add_argument(
        '--spread-threshold',
        type=float,
        default=15.0,
        help='Minimum percentage spread to alert on (default: 15.0)'
    )
    
    parser.add_argument(
        '--min-spread',
        type=int,
        default=1000,
        help='Minimum absolute spread in cents (default: 1000 = $10.00)'
    )
    
    parser.add_argument(
        '--output',
        help='Output file path (default: stdout)'
    )
    
    args = parser.parse_args()
    
    # Scan for opportunities
    try:
        opportunities = scan_arbitrage_opportunities(
            db_path=args.db_path,
            spread_threshold=args.spread_threshold,
            min_spread_minor=args.min_spread
        )
        
        # Format summary
        summary = format_arbitrage_summary(opportunities)
        
        # Output
        if args.output:
            with open(args.output, 'w') as f:
                f.write(summary)
            print(f"Arbitrage alert written to {args.output}")
        else:
            print(summary)
            
        # Exit with non-zero if opportunities found (for CI/CD integration)
        if opportunities:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
