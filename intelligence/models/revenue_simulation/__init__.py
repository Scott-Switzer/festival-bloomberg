"""
Revenue Simulation Model - Monte Carlo forecasting for festival revenue scenarios.
Calculates ticket revenue, ancillary revenue, and contribution margins under uncertainty.
"""

import polars as pl
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RevenueScenario:
    """Revenue scenario parameters"""
    capacity: int
    expected_attendance: int
    ticket_tiers: Dict[str, Dict[str, any]]  # {tier_name: {price, quantity}}
    vip_mix: float
    sponsorship_commitments: float
    per_capita_fnb_spending: float
    per_capita_merch_spending: float
    artist_cost_range: Tuple[float, float]
    production_costs: float
    weather_assumption: Optional[str] = None


@dataclass
class RevenueResult:
    """Revenue calculation results"""
    ticket_revenue: float
    ancillary_revenue: float
    total_revenue: float
    artist_costs: float
    contribution_margin: float
    p10_downside: float
    p50_base_case: float
    p90_upside: float
    profitability_probability: float
    break_even_attendance: int
    break_even_ticket_price: float
    revenue_at_risk_weather: float


class RevenueSimulationModel:
    """
    Simulates festival revenue under different scenarios using Monte Carlo methods.
    
    Accounts for uncertainty in attendance, ticket sales, weather, and costs.
    """
    
    def __init__(self, num_simulations: int = 10000):
        """
        Initialize the simulation model.
        
        Args:
            num_simulations: Number of Monte Carlo simulations to run
        """
        self.num_simulations = num_simulations
    
    def calculate_ticket_revenue(
        self,
        ticket_tiers: Dict[str, Dict[str, any]],
        attendance: int,
        sell_through_rate: float = 0.95,
    ) -> float:
        """
        Calculate ticket revenue.
        
        Args:
            ticket_tiers: Dictionary of ticket tiers with prices and quantities
            attendance: Expected attendance
            sell_through_rate: Expected sell-through rate
        
        Returns:
            Ticket revenue
        """
        total_revenue = 0.0
        
        for tier_name, tier_data in ticket_tiers.items():
            price = tier_data["price"]
            quantity = tier_data.get("quantity", attendance)
            
            # Adjust for actual attendance and sell-through
            actual_quantity = min(quantity, attendance) * sell_through_rate
            tier_revenue = actual_quantity * price
            total_revenue += tier_revenue
        
        return total_revenue
    
    def calculate_ancillary_revenue(
        self,
        attendance: int,
        per_capita_fnb: float,
        per_capita_merch: float,
        festival_revenue_share: float = 0.3,
    ) -> float:
        """
        Calculate ancillary revenue (food, beverage, merchandise).
        
        Args:
            attendance: Expected attendance
            per_capita_fnb: Per-capita food & beverage spending
            per_capita_merch: Per-capita merchandise spending
            festival_revenue_share: Festival's share of ancillary revenue
        
        Returns:
            Ancillary revenue
        """
        total_per_capita = per_capita_fnb + per_capita_merch
        total_ancillary = attendance * total_per_capita
        festival_share = total_ancillary * festival_revenue_share
        
        return festival_share
    
    def calculate_artist_costs(
        self,
        lineup_size: int,
        cost_range: Tuple[float, float],
    ) -> float:
        """
        Calculate total artist costs.
        
        Args:
            lineup_size: Number of artists in lineup
            cost_range: (min_cost, max_cost) per artist
        
        Returns:
            Total artist costs
        """
        min_cost, max_cost = cost_range
        avg_cost_per_artist = (min_cost + max_cost) / 2
        total_cost = avg_cost_per_artist * lineup_size
        
        return total_cost
    
    def calculate_contribution_margin(
        self,
        total_revenue: float,
        artist_costs: float,
        production_costs: float,
        variable_costs: float = 0.0,
    ) -> float:
        """
        Calculate contribution margin.
        
        Args:
            total_revenue: Total revenue
            artist_costs: Total artist costs
            production_costs: Production costs
            variable_costs: Variable costs
        
        Returns:
            Contribution margin
        """
        total_costs = artist_costs + production_costs + variable_costs
        margin = total_revenue - total_costs
        
        return margin
    
    def run_monte_carlo_simulation(
        self,
        scenario: RevenueScenario,
        lineup_size: int,
        attendance_std: float = 0.1,
        sell_through_std: float = 0.05,
        weather_impact_std: float = 0.05,
    ) -> RevenueResult:
        """
        Run Monte Carlo simulation for revenue forecasting.
        
        Args:
            scenario: Revenue scenario parameters
            lineup_size: Number of artists in lineup
            attendance_std: Standard deviation of attendance (as ratio)
            sell_through_std: Standard deviation of sell-through rate
            weather_impact_std: Standard deviation of weather impact
        
        Returns:
            RevenueResult with simulation outcomes
        """
        np.random.seed(42)
        
        # Simulate attendance
        attendance_samples = np.random.normal(
            scenario.expected_attendance,
            scenario.expected_attendance * attendance_std,
            self.num_simulations,
        )
        attendance_samples = np.clip(attendance_samples, 0, scenario.capacity)
        
        # Simulate sell-through rates
        sell_through_samples = np.random.normal(
            0.95,
            sell_through_std,
            self.num_simulations,
        )
        sell_through_samples = np.clip(sell_through_samples, 0.5, 1.0)
        
        # Simulate weather impact
        weather_samples = np.random.normal(
            1.0,
            weather_impact_std,
            self.num_simulations,
        )
        weather_samples = np.clip(weather_samples, 0.7, 1.0)
        
        # Calculate revenues for each simulation
        ticket_revenues = []
        ancillary_revenues = []
        total_revenues = []
        contribution_margins = []
        
        for i in range(self.num_simulations):
            attendance = attendance_samples[i]
            sell_through = sell_through_samples[i]
            weather_factor = weather_samples[i]
            
            # Adjust attendance for weather
            weather_adjusted_attendance = attendance * weather_factor
            
            # Calculate revenues
            ticket_rev = self.calculate_ticket_revenue(
                scenario.ticket_tiers,
                weather_adjusted_attendance,
                sell_through,
            )
            
            ancillary_rev = self.calculate_ancillary_revenue(
                weather_adjusted_attendance,
                scenario.per_capita_fnb_spending,
                scenario.per_capita_merch_spending,
            )
            
            total_rev = ticket_rev + ancillary_rev + scenario.sponsorship_commitments
            
            artist_costs = self.calculate_artist_costs(lineup_size, scenario.artist_cost_range)
            margin = self.calculate_contribution_margin(
                total_rev,
                artist_costs,
                scenario.production_costs,
            )
            
            ticket_revenues.append(ticket_rev)
            ancillary_revenues.append(ancillary_rev)
            total_revenues.append(total_rev)
            contribution_margins.append(margin)
        
        # Calculate percentiles
        p10 = np.percentile(total_revenues, 10)
        p50 = np.percentile(total_revenues, 50)
        p90 = np.percentile(total_revenues, 90)
        
        # Calculate profitability probability
        profitability_prob = np.mean(np.array(contribution_margins) > 0)
        
        # Calculate base case (using expected values)
        base_ticket_revenue = self.calculate_ticket_revenue(
            scenario.ticket_tiers,
            scenario.expected_attendance,
            0.95,
        )
        base_ancillary_revenue = self.calculate_ancillary_revenue(
            scenario.expected_attendance,
            scenario.per_capita_fnb_spending,
            scenario.per_capita_merch_spending,
        )
        base_total_revenue = base_ticket_revenue + base_ancillary_revenue + scenario.sponsorship_commitments
        base_artist_costs = self.calculate_artist_costs(lineup_size, scenario.artist_cost_range)
        base_margin = self.calculate_contribution_margin(
            base_total_revenue,
            base_artist_costs,
            scenario.production_costs,
        )
        
        # Calculate break-even attendance
        break_even_attendance = self.calculate_break_even_attendance(
            scenario.ticket_tiers,
            base_artist_costs + scenario.production_costs,
            scenario.per_capita_fnb_spending,
            scenario.per_capita_merch_spending,
            scenario.sponsorship_commitments,
        )
        
        # Calculate break-even ticket price
        break_even_ticket_price = self.calculate_break_even_ticket_price(
            scenario.expected_attendance,
            base_artist_costs + scenario.production_costs,
            scenario.per_capita_fnb_spending,
            scenario.per_capita_merch_spending,
            scenario.sponsorship_commitments,
            scenario.ticket_tiers,
        )
        
        # Calculate revenue at risk from weather
        revenue_at_risk_weather = base_total_revenue - p10
        
        return RevenueResult(
            ticket_revenue=base_ticket_revenue,
            ancillary_revenue=base_ancillary_revenue,
            total_revenue=base_total_revenue,
            artist_costs=base_artist_costs,
            contribution_margin=base_margin,
            p10_downside=p10,
            p50_base_case=p50,
            p90_upside=p90,
            profitability_probability=profitability_prob,
            break_even_attendance=break_even_attendance,
            break_even_ticket_price=break_even_ticket_price,
            revenue_at_risk_weather=revenue_at_risk_weather,
        )
    
    def calculate_break_even_attendance(
        self,
        ticket_tiers: Dict[str, Dict[str, any]],
        fixed_costs: float,
        per_capita_fnb: float,
        per_capita_merch: float,
        sponsorship: float,
    ) -> int:
        """
        Calculate break-even attendance.
        
        Args:
            ticket_tiers: Ticket tier configuration
            fixed_costs: Fixed costs (artist + production)
            per_capita_fnb: Per-capita F&B spending
            per_capita_merch: Per-capita merchandise spending
            sponsorship: Sponsorship commitments
        
        Returns:
            Break-even attendance
        """
        # Calculate average ticket price
        total_tickets = sum(tier["quantity"] for tier in ticket_tiers.values())
        total_ticket_value = sum(
            tier["price"] * tier["quantity"]
            for tier in ticket_tiers.values()
        )
        avg_ticket_price = total_ticket_value / total_tickets if total_tickets > 0 else 0
        
        # Per-attendee revenue
        per_attendee_revenue = avg_ticket_price + per_capita_fnb + per_capita_merch
        
        # Break-even attendance
        if per_attendee_revenue > 0:
            break_even = (fixed_costs - sponsorship) / per_attendee_revenue
        else:
            break_even = float('inf')
        
        return int(np.ceil(break_even))
    
    def calculate_break_even_ticket_price(
        self,
        attendance: int,
        fixed_costs: float,
        per_capita_fnb: float,
        per_capita_merch: float,
        sponsorship: float,
        ticket_tiers: Dict[str, Dict[str, any]],
    ) -> float:
        """
        Calculate break-even ticket price.
        
        Args:
            attendance: Expected attendance
            fixed_costs: Fixed costs
            per_capita_fnb: Per-capita F&B spending
            per_capita_merch: Per-capita merchandise spending
            sponsorship: Sponsorship commitments
            ticket_tiers: Ticket tier configuration
        
        Returns:
            Break-even ticket price
        """
        # Revenue from ancillary and sponsorship
        ancillary_revenue = attendance * (per_capita_fnb + per_capita_merch)
        other_revenue = ancillary_revenue + sponsorship
        
        # Required ticket revenue
        required_ticket_revenue = fixed_costs - other_revenue
        
        # Break-even ticket price
        if attendance > 0:
            break_even_price = required_ticket_revenue / attendance
        else:
            break_even_price = float('inf')
        
        return max(break_even_price, 0)
    
    def calculate_artist_sensitivity(
        self,
        scenario: RevenueScenario,
        lineup_size: int,
        artist_costs: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Calculate revenue sensitivity to each artist.
        
        Args:
            scenario: Revenue scenario
            lineup_size: Number of artists
            artist_costs: Dict mapping artist_id to cost
        
        Returns:
            Dict mapping artist_id to sensitivity score
        """
        base_result = self.run_monte_carlo_simulation(scenario, lineup_size)
        base_margin = base_result.contribution_margin
        
        sensitivities = {}
        
        for artist_id, cost in artist_costs.items():
            # Remove artist and recalculate
            new_lineup_size = lineup_size - 1
            new_cost_range = (cost * 0.9, cost * 1.1)  # Adjusted range
            
            new_scenario = RevenueScenario(
                capacity=scenario.capacity,
                expected_attendance=scenario.expected_attendance,
                ticket_tiers=scenario.ticket_tiers,
                vip_mix=scenario.vip_mix,
                sponsorship_commitments=scenario.sponsorship_commitments,
                per_capita_fnb_spending=scenario.per_capita_fnb_spending,
                per_capita_merch_spending=scenario.per_capita_merch_spending,
                artist_cost_range=new_cost_range,
                production_costs=scenario.production_costs,
                weather_assumption=scenario.weather_assumption,
            )
            
            new_result = self.run_monte_carlo_simulation(new_scenario, new_lineup_size)
            margin_change = base_margin - new_result.contribution_margin
            
            sensitivities[artist_id] = margin_change
        
        return sensitivities
