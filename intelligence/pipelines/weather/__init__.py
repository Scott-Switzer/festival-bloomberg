"""
National Weather Service (NWS) and NOAA NCEI API pipeline for weather data.
"""

import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field


class NWSClient(BaseModel):
    """National Weather Service API client"""
    base_url: str = "https://api.weather.gov/"
    user_agent: str = Field(..., description="User-Agent string (required by NWS)")
    
    def get_point_forecast(self, latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
        """
        Get forecast for a specific point.
        
        Args:
            latitude: Latitude
            longitude: Longitude
        
        Returns:
            Forecast data or None
        """
        url = f"{self.base_url}points/{latitude},{longitude}"
        headers = {"User-Agent": self.user_agent}
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS forecast for {latitude}, {longitude}: {e}")
            return None
    
    def get_alerts(self, state: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get weather alerts.
        
        Args:
            state: Two-letter state code (optional)
        
        Returns:
            Alert data or None
        """
        url = f"{self.base_url}alerts"
        if state:
            url = f"{self.base_url}alerts?area={state}"
        headers = {"User-Agent": self.user_agent}
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS alerts: {e}")
            return None


class NOAAClient(BaseModel):
    """NOAA NCEI API client for historical weather data"""
    base_url: str = "https://www.ncdc.noaa.gov/cdo-web/api/v2/"
    api_key: str = Field(..., description="NOAA API key")
    
    def get_stations(
        self,
        location_id: Optional[str] = None,
        limit: int = 1000,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get weather stations.
        
        Args:
            location_id: Location ID (FIPS code, etc.)
            limit: Maximum number of results
        
        Returns:
            List of station dictionaries or None
        """
        url = f"{self.base_url}stations"
        headers = {"token": self.api_key}
        params = {"limit": limit}
        if location_id:
            params["locationid"] = location_id
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except requests.RequestException as e:
            print(f"Error fetching NOAA stations: {e}")
            return []
    
    def get_historical_data(
        self,
        station_id: str,
        start_date: str,
        end_date: str,
        datasetid: str = "GHCND",
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get historical weather data for a station.
        
        Args:
            station_id: Station ID
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            datasetid: Dataset ID (default: GHCND for daily summaries)
        
        Returns:
            List of weather records or None
        """
        url = f"{self.base_url}data"
        headers = {"token": self.api_key}
        params = {
            "datasetid": datasetid,
            "stationid": station_id,
            "startdate": start_date,
            "enddate": end_date,
            "limit": 1000,
        }
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except requests.RequestException as e:
            print(f"Error fetching NOAA historical data: {e}")
            return []


def calculate_weather_risk(historical_data: List[Dict[str, Any]], forecast_data: Optional[Dict] = None) -> Dict[str, float]:
    """
    Calculate weather risk metrics from historical and forecast data.
    
    Args:
        historical_data: Historical weather records
        forecast_data: Forecast data from NWS
    
    Returns:
        Dictionary with weather risk metrics
    """
    if not historical_data:
        return {
            "weather_risk_score": 50.0,
            "heat_stress_score": 50.0,
            "rain_disruption_probability": 0.5,
        }
    
    # Extract temperature and precipitation data
    temperatures = []
    precipitations = []
    
    for record in historical_data:
        if record.get("datatype") == "TMAX":
            temperatures.append(record.get("value", 0) / 10)  # Convert to Celsius
        elif record.get("datatype") == "PRCP":
            precipitations.append(record.get("value", 0) / 10)  # Convert to mm
    
    # Calculate metrics
    avg_temp = sum(temperatures) / len(temperatures) if temperatures else 20
    avg_precip = sum(precipitations) / len(precipitations) if precipitations else 0
    
    # Heat stress score (higher temp = higher risk)
    heat_stress_score = min(max(avg_temp / 40 * 100, 0), 100)
    
    # Rain disruption probability (higher precip = higher risk)
    rain_disruption_probability = min(avg_precip / 20, 1.0)
    
    # Overall weather risk score
    weather_risk_score = (heat_stress_score + rain_disruption_probability * 100) / 2
    
    return {
        "weather_risk_score": weather_risk_score,
        "heat_stress_score": heat_stress_score,
        "rain_disruption_probability": rain_disruption_probability,
        "avg_temperature": avg_temp,
        "avg_precipitation": avg_precip,
    }
