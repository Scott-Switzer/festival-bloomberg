"""
Monid.ai integration for Festival Intelligence Terminal.
Provides agentic tool discovery and execution capabilities.
"""
import os
import requests
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from config import get_config


@dataclass
class MonidTool:
    """Represents a discovered Monid tool."""
    id: str
    name: str
    description: str
    pricing: Dict[str, Any]
    category: Optional[str] = None


class MonidClient:
    """Client for interacting with Monid.ai API."""
    
    def __init__(self, api_key: Optional[str] = None):
        config = get_config()
        self.api_key = api_key or config.get_api_key("monid")
        self.base_url = config.monid_config.base_url if config.monid_config else "https://api.monid.ai"
        self.workspace_id = None
        
        if not self.api_key:
            print("Warning: MONID_API_KEY not found, Monid.ai features will be limited")
            # Don't raise error for MVP - allow graceful degradation
            return
        
        # Authenticate and get workspace
        self._authenticate()
    
    def _authenticate(self):
        """Authenticate and get workspace ID."""
        if not self.api_key:
            return
            
        try:
            response = requests.get(
                f"{self.base_url}/v1/auth/workspaces",
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
            response.raise_for_status()
            
            workspaces = response.json()
            print(f"Debug - Workspaces response: {workspaces}")
            
            # Handle different response structures
            if isinstance(workspaces, dict):
                # Response might be {"workspaces": [...]}
                workspaces = workspaces.get("workspaces", [])
            
            if workspaces and len(workspaces) > 0:
                self.workspace_id = workspaces[0].get("id")
                print(f"✓ Monid.ai authenticated, workspace: {self.workspace_id}")
            else:
                print("⚠ No workspaces found in Monid.ai account")
                
        except Exception as e:
            print(f"Monid.ai authentication failed: {e}")
            # Don't raise error for MVP - allow graceful degradation
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with authentication."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.workspace_id:
            headers["x-workspace-id"] = self.workspace_id
        return headers
    
    def discover(self, query: str) -> List[MonidTool]:
        """
        Discover tools using Monid.ai agentic capabilities.
        
        Args:
            query: Search query for tools
            
        Returns:
            List of discovered tools
        """
        if not self.api_key:
            print("Monid.ai not configured, returning placeholder tools")
            return [
                MonidTool(
                    id="placeholder_1",
                    name="Artist Data Collector",
                    description="Collects comprehensive artist data from multiple sources",
                    pricing={"cost": 0.01, "currency": "USD"},
                    category="artist_intelligence"
                ),
                MonidTool(
                    id="placeholder_2", 
                    name="Festival Intelligence",
                    description="Gathers festival lineup and performance data",
                    pricing={"cost": 0.02, "currency": "USD"},
                    category="festival_intelligence"
                )
            ]
        
        try:
            response = requests.get(
                f"{self.base_url}/v1/tools/discover",
                headers=self._get_headers(),
                params={"query": query}
            )
            response.raise_for_status()
            
            tools_data = response.json()
            return [
                MonidTool(
                    id=tool.get("id"),
                    name=tool.get("name"),
                    description=tool.get("description"),
                    pricing=tool.get("pricing", {}),
                    category=tool.get("category")
                )
                for tool in tools_data.get("tools", [])
            ]
        except Exception as e:
            print(f"Error discovering tools: {e}")
            return []
    
    def run(self, tool_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool using Monid.ai.
        
        Args:
            tool_id: ID of the tool to execute
            parameters: Parameters for the tool
            
        Returns:
            Tool execution result
        """
        if not self.api_key:
            print("Monid.ai not configured, returning placeholder result")
            return {
                "status": "simulated",
                "result": {"message": "Monid.ai not configured, returning simulated result"},
                "tool_id": tool_id,
                "parameters": parameters
            }
        
        try:
            response = requests.post(
                f"{self.base_url}/v1/tools/{tool_id}/run",
                headers=self._get_headers(),
                json=parameters
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error running tool: {e}")
            raise
    
    def get_balance(self) -> Dict[str, Any]:
        """Get Monid.ai wallet balance."""
        if not self.api_key:
            return {"balance": 0, "currency": "USD", "status": "not_configured"}
        
        try:
            response = requests.get(
                f"{self.base_url}/v1/balance",
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error getting balance: {e}")
            return {"balance": 0, "currency": "USD", "error": str(e)}


class FestivalIntelligenceAgent:
    """Agentic system for festival intelligence data collection."""
    
    def collect_artist_data(self, artist_name: str) -> Dict[str, Any]:
        """Collect comprehensive artist data using Monid.ai tools."""
        try:
            client = MonidClient()
            # Discover relevant tools
            tools = client.discover(f"artist data for {artist_name}")
            
            if not tools:
                return {
                    "status": "no_tools",
                    "artist_name": artist_name,
                    "message": "No tools discovered for artist data collection"
                }
            
            # Execute the most relevant tool
            best_tool = tools[0]
            result = client.run(best_tool.id, {"artist_name": artist_name})
            
            return {
                "status": "success",
                "artist_name": artist_name,
                "tool_used": best_tool.name,
                "data": result
            }
        except Exception as e:
            print(f"Error collecting artist data: {e}")
            return {
                "status": "error",
                "artist_name": artist_name,
                "error": str(e)
            }
    
    def collect_festival_data(self, festival_name: str) -> Dict[str, Any]:
        """Collect festival data using Monid.ai tools."""
        try:
            client = MonidClient()
            tools = client.discover(f"festival data for {festival_name}")
            
            if not tools:
                return {
                    "status": "no_tools",
                    "festival_name": festival_name,
                    "message": "No tools discovered for festival data collection"
                }
            
            best_tool = tools[0]
            result = client.run(best_tool.id, {"festival_name": festival_name})
            
            return {
                "status": "success",
                "festival_name": festival_name,
                "tool_used": best_tool.name,
                "data": result
            }
        except Exception as e:
            print(f"Error collecting festival data: {e}")
            return {
                "status": "error",
                "festival_name": festival_name,
                "error": str(e)
            }
    
    def collect_social_sentiment(self, artist_name: str, platform: str = "twitter") -> Dict[str, Any]:
        """Collect social media sentiment for an artist."""
        try:
            client = MonidClient()
            tools = client.discover(f"social sentiment analysis for {artist_name} on {platform}")
            
            if not tools:
                return {
                    "status": "no_tools",
                    "artist_name": artist_name,
                    "platform": platform,
                    "message": "No tools discovered for sentiment analysis"
                }
            
            best_tool = tools[0]
            result = client.run(best_tool.id, {"artist_name": artist_name, "platform": platform})
            
            return {
                "status": "success",
                "artist_name": artist_name,
                "platform": platform,
                "tool_used": best_tool.name,
                "data": result
            }
        except Exception as e:
            print(f"Error collecting social sentiment: {e}")
            return {
                "status": "error",
                "artist_name": artist_name,
                "platform": platform,
                "error": str(e)
            }


class FestivalIntelligenceAgent:
    """Agentic system for festival intelligence data collection."""
    
    def __init__(self):
        self.monid = MonidClient()
    
    def collect_artist_data(self, artist_name: str) -> Dict[str, Any]:
        """Collect comprehensive artist data using Monid.ai tools."""
        try:
            tools = self.monid.discover(f"artist data for {artist_name}")
            
            if not tools:
                return {
                    "status": "no_tools",
                    "artist_name": artist_name,
                    "message": "No tools discovered for artist data collection"
                }
            
            best_tool = tools[0]
            result = self.monid.run(best_tool.id, {"artist_name": artist_name})
            
            return {
                "status": "success",
                "artist_name": artist_name,
                "tool_used": best_tool.name,
                "data": result
            }
        except Exception as e:
            print(f"Error collecting artist data: {e}")
            return {
                "status": "error",
                "artist_name": artist_name,
                "error": str(e)
            }
    
    def collect_festival_data(self, festival_name: str) -> Dict[str, Any]:
        """Collect festival data using Monid.ai tools."""
        try:
            tools = self.monid.discover(f"festival data for {festival_name}")
            
            if not tools:
                return {
                    "status": "no_tools",
                    "festival_name": festival_name,
                    "message": "No tools discovered for festival data collection"
                }
            
            best_tool = tools[0]
            result = self.monid.run(best_tool.id, {"festival_name": festival_name})
            
            return {
                "status": "success",
                "festival_name": festival_name,
                "tool_used": best_tool.name,
                "data": result
            }
        except Exception as e:
            print(f"Error collecting festival data: {e}")
            return {
                "status": "error",
                "festival_name": festival_name,
                "error": str(e)
            }
    
    def collect_social_sentiment(self, artist_name: str, platform: str = "twitter") -> Dict[str, Any]:
        """Collect social media sentiment for an artist."""
        try:
            tools = self.monid.discover(f"social sentiment analysis for {artist_name} on {platform}")
            
            if not tools:
                return {
                    "status": "no_tools",
                    "artist_name": artist_name,
                    "platform": platform,
                    "message": "No tools discovered for sentiment analysis"
                }
            
            best_tool = tools[0]
            result = self.monid.run(best_tool.id, {"artist_name": artist_name, "platform": platform})
            
            return {
                "status": "success",
                "artist_name": artist_name,
                "platform": platform,
                "tool_used": best_tool.name,
                "data": result
            }
        except Exception as e:
            print(f"Error collecting social sentiment: {e}")
            return {
                "status": "error",
                "artist_name": artist_name,
                "platform": platform,
                "error": str(e)
            }
