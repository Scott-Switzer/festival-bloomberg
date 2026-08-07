"""
LLM Extraction System with Python Instructor
Implements structured data extraction from unstructured content per Festival Bloomberg spec
"""
import logging
import os
from typing import Optional, Dict, Any, List, Type, TypeVar
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, validator
import instructor
from openai import OpenAI

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


class ExtractionModel(Enum):
    """Supported LLM models for extraction"""
    # NVIDIA Free Models (Recommended)
    NVIDIA_LLAMA_3_1_8B = "meta/llama-3.1-8b-instruct"
    NVIDIA_MISTRAL_7B = "mistralai/mistral-7b-instruct-v0.3"
    NVIDIA_MIXTRAL_8x7B = "mistralai/mixtral-8x7b-instruct-v0.1"
    NVIDIA_NEMOTRON_70B = "nvidia/nemotron-4-340b-instruct"
    
    # OpenAI Models (Alternative)
    GPT4O = "gpt-4o"
    GPT4O_MINI = "gpt-4o-mini"
    GPT35_TURBO = "gpt-3.5-turbo"
    
    # Anthropic Models (Alternative)
    CLAUDE_3_5_SONNET = "claude-3-5-sonnet-20241022"
    
    @classmethod
    def get_default(cls) -> 'ExtractionModel':
        """Get default model based on available API keys"""
        if os.getenv('NVIDIA_API_KEY'):
            # Use highest quality NVIDIA model since it's free
            return cls.NVIDIA_NEMOTRON_70B
        elif os.getenv('OPENAI_API_KEY'):
            return cls.GPT4O_MINI
        else:
            return cls.NVIDIA_NEMOTRON_70B


# ============================================================================
# Pydantic Schemas for Festival Bloomberg Entities
# ============================================================================

class ArtistExtraction(BaseModel):
    """Artist extraction schema"""
    name: str = Field(..., description="Artist name")
    sort_name: Optional[str] = Field(None, description="Normalized sort name")
    genres: List[str] = Field(default_factory=list, description="List of genres")
    country: Optional[str] = Field(None, description="Country of origin")
    city: Optional[str] = Field(None, description="City of origin")
    formed_year: Optional[int] = Field(None, description="Year formed")
    disbanded_year: Optional[int] = Field(None, description="Year disbanded")
    artist_type: Optional[str] = Field(None, description="Type: person, group, ensemble")
    website: Optional[str] = Field(None, description="Official website")
    description: Optional[str] = Field(None, description="Brief description")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")
    
    @validator('formed_year')
    def validate_formed_year(cls, v):
        if v is not None and (v < 1900 or v > datetime.now().year + 1):
            return None
        return v
    
    @validator('disbanded_year')
    def validate_disbanded_year(cls, v, values):
        if v is not None:
            formed = values.get('formed_year')
            if formed is not None and v < formed:
                return None
        return v


class FestivalExtraction(BaseModel):
    """Festival extraction schema"""
    name: str = Field(..., description="Festival name")
    city: Optional[str] = Field(None, description="City where festival takes place")
    region: Optional[str] = Field(None, description="Region/state")
    country: Optional[str] = Field(None, description="Country")
    genres: List[str] = Field(default_factory=list, description="Primary genres")
    festival_type: Optional[str] = Field(None, description="Type: music, arts, mixed")
    typical_month: Optional[int] = Field(None, description="Typical month (1-12)")
    capacity: Optional[int] = Field(None, description="Estimated capacity")
    website: Optional[str] = Field(None, description="Official website")
    description: Optional[str] = Field(None, description="Brief description")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")
    
    @validator('typical_month')
    def validate_typical_month(cls, v):
        if v is not None and (v < 1 or v > 12):
            return None
        return v


class LineupAppearance(BaseModel):
    """Lineup appearance extraction schema"""
    artist_name: str = Field(..., description="Artist name")
    festival_name: str = Field(..., description="Festival name")
    year: Optional[int] = Field(None, description="Performance year")
    stage: Optional[str] = Field(None, description="Stage name")
    day: Optional[str] = Field(None, description="Day of performance")
    position: Optional[str] = Field(None, description="Position: headliner, sub-headliner, supporting")
    set_time: Optional[str] = Field(None, description="Set time")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")


class AgencyRelationship(BaseModel):
    """Agency relationship extraction schema"""
    artist_name: str = Field(..., description="Artist name")
    organization_name: str = Field(..., description="Organization/agency name")
    relationship_type: str = Field(..., description="Type: booking, management, label, etc.")
    territory: Optional[str] = Field(None, description="Territory covered")
    exclusive: Optional[bool] = Field(None, description="Whether relationship is exclusive")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")


class VenueExtraction(BaseModel):
    """Venue extraction schema"""
    name: str = Field(..., description="Venue name")
    city: Optional[str] = Field(None, description="City")
    region: Optional[str] = Field(None, description="Region/state")
    country: Optional[str] = Field(None, description="Country")
    capacity: Optional[int] = Field(None, description="Capacity")
    venue_type: Optional[str] = Field(None, description="Type: indoor, outdoor, arena, stadium")
    address: Optional[str] = Field(None, description="Street address")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")


class ContactExtraction(BaseModel):
    """Contact information extraction schema"""
    name: str = Field(..., description="Contact name")
    role: Optional[str] = Field(None, description="Role: talent buyer, booking agent, manager, etc.")
    company: Optional[str] = Field(None, description="Company name")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")


# ============================================================================
# LLM Extractor
# ============================================================================

@dataclass
class ExtractionResult:
    """Result of LLM extraction"""
    success: bool
    data: Optional[BaseModel]
    error: Optional[str]
    model_used: str
    tokens_used: int
    cost_estimate: float
    extraction_time: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class LLMExtractor:
    """
    LLM-based data extraction using Python Instructor
    Implements Festival Bloomberg LLM extraction requirements
    """
    
    def __init__(self, 
                 api_key: Optional[str] = None,
                 model: Optional[ExtractionModel] = None,
                 base_url: Optional[str] = None,
                 use_nvidia: bool = True):
        """
        Initialize LLM extractor
        
        Args:
            api_key: API key (defaults to NVIDIA_API_KEY or OPENAI_API_KEY from env)
            model: Model to use for extraction (defaults based on available API key)
            base_url: Optional custom base URL
            use_nvidia: Whether to use NVIDIA API (default True)
        """
        # Determine API key
        if api_key is None:
            if use_nvidia:
                api_key = os.getenv('NVIDIA_API_KEY')
            if not api_key:
                api_key = os.getenv('OPENAI_API_KEY')
        
        if not api_key:
            raise ValueError("No API key provided. Set NVIDIA_API_KEY or OPENAI_API_KEY environment variable.")
        
        # Determine model
        if model is None:
            model = ExtractionModel.get_default()
        
        self.model = model
        self.use_nvidia = use_nvidia
        
        # Determine base URL
        if base_url is None:
            if use_nvidia and os.getenv('NVIDIA_API_KEY'):
                base_url = "https://integrate.api.nvidia.com/v1"
        
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        
        # Patch the client with Instructor only if not using NVIDIA
        # NVIDIA API doesn't support function calling used by Instructor
        if use_nvidia:
            self._instructor_client = None
            logger.warning("Instructor disabled for NVIDIA API (function calling not supported)")
        else:
            self._instructor_client = instructor.from_openai(self._client)
        
        # Cost tracking
        self._cost_tracker = {
            'total_tokens': 0,
            'total_cost': 0.0,
            'extraction_count': 0
        }
        
        logger.info(f"LLM extractor initialized with model: {model.value}, base_url: {base_url}")
    
    def _estimate_cost(self, tokens: int, model: ExtractionModel) -> float:
        """Estimate cost based on token count and model"""
        # Pricing (as of 2024) - approximate
        pricing = {
            # NVIDIA models are FREE for developer program members
            ExtractionModel.NVIDIA_LLAMA_3_1_8B: {'input': 0.0, 'output': 0.0},
            ExtractionModel.NVIDIA_MISTRAL_7B: {'input': 0.0, 'output': 0.0},
            ExtractionModel.NVIDIA_MIXTRAL_8x7B: {'input': 0.0, 'output': 0.0},
            ExtractionModel.NVIDIA_NEMOTRON_70B: {'input': 0.0, 'output': 0.0},
            # OpenAI models (per 1K tokens)
            ExtractionModel.GPT4O: {'input': 0.005, 'output': 0.015},
            ExtractionModel.GPT4O_MINI: {'input': 0.00015, 'output': 0.0006},
            ExtractionModel.GPT35_TURBO: {'input': 0.0005, 'output': 0.0015},
            # Anthropic models (per 1K tokens)
            ExtractionModel.CLAUDE_3_5_SONNET: {'input': 0.003, 'output': 0.015}
        }
        
        model_pricing = pricing.get(model, pricing[ExtractionModel.NVIDIA_LLAMA_3_1_8B])
        
        # Assume 50% input, 50% output for estimation
        input_cost = (tokens * 0.5 / 1000) * model_pricing['input']
        output_cost = (tokens * 0.5 / 1000) * model_pricing['output']
        
        return input_cost + output_cost
    
    def _get_schema_fields(self, schema: Type[T]) -> str:
        """Get field names and types from Pydantic schema for prompt"""
        if hasattr(schema, '__fields__'):
            field_info = []
            for field_name, field in schema.__fields__.items():
                field_type = field.annotation.__name__ if hasattr(field.annotation, '__name__') else str(field.annotation)
                field_info.append(f"{field_name} ({field_type})")
            return ', '.join(field_info)
        return 'name, description, and other relevant fields'
    
    def _extract_without_instructor(self, 
                                   content: str, 
                                   schema: Type[T], 
                                   prompt: str, 
                                   max_retries: int) -> T:
        """Extract using standard OpenAI client without Instructor (for NVIDIA)"""
        import json
        import re
        
        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.model.value,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2000
                )
                
                # Extract JSON from response
                response_text = response.choices[0].message.content.strip()
                
                # Try to parse JSON
                # Handle potential markdown code blocks
                if response_text.startswith('```json'):
                    response_text = response_text[7:]
                elif response_text.startswith('```'):
                    response_text = response_text[3:]
                if response_text.endswith('```'):
                    response_text = response_text[:-3]
                
                response_text = response_text.strip()
                data = json.loads(response_text)
                
                # Clean data for schema validation
                data = self._clean_data_for_schema(data, schema)
                
                # Validate with Pydantic schema
                return schema(**data)
                
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise
                # Retry with more explicit instruction
                prompt = f"{prompt}\n\nIMPORTANT: Return ONLY valid JSON, no markdown, no explanations."
            except Exception as e:
                logger.warning(f"Extraction error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise
    
    def _clean_data_for_schema(self, data: dict, schema: Type[T]) -> dict:
        """Clean extracted data to match schema types"""
        if not hasattr(schema, '__fields__'):
            return data
        
        cleaned = {}
        for field_name, field in schema.__fields__.items():
            if field_name not in data:
                # Use default value if available, otherwise None
                if field.default is not None and not callable(field.default):
                    cleaned[field_name] = field.default
                else:
                    cleaned[field_name] = None
                continue
            
            value = data[field_name]
            
            # Handle integer fields
            if field.annotation == int:
                if isinstance(value, str):
                    # Extract numbers from text (handle commas and spaces)
                    numbers = re.findall(r'\d+[,\d]*', str(value))
                    if numbers:
                        # Remove commas and convert to int
                        cleaned[field_name] = int(numbers[0].replace(',', ''))
                        logger.debug(f"Converted '{value}' to {cleaned[field_name]} for field {field_name}")
                    else:
                        cleaned[field_name] = field.default if field.default is not None and not callable(field.default) else None
                        logger.warning(f"Could not extract number from '{value}' for field {field_name}")
                elif isinstance(value, (int, float)):
                    cleaned[field_name] = int(value)
                else:
                    cleaned[field_name] = field.default if field.default is not None and not callable(field.default) else None
            # Handle float fields
            elif field.annotation == float:
                if isinstance(value, str):
                    numbers = re.findall(r'\d+\.?\d*', str(value))
                    if numbers:
                        cleaned[field_name] = float(numbers[0])
                    else:
                        cleaned[field_name] = field.default if field.default is not None and not callable(field.default) else None
                elif isinstance(value, (int, float)):
                    cleaned[field_name] = float(value)
                else:
                    cleaned[field_name] = field.default if field.default is not None and not callable(field.default) else None
            else:
                cleaned[field_name] = value
        
        return cleaned
    
    def extract(self, 
                content: str,
                schema: Type[T],
                prompt: Optional[str] = None,
                max_retries: int = 3) -> ExtractionResult:
        """
        Extract structured data from content
        
        Args:
            content: Unstructured content to extract from
            schema: Pydantic schema for extraction
            prompt: Optional custom prompt
            max_retries: Maximum retry attempts
            
        Returns:
            ExtractionResult
        """
        start_time = datetime.utcnow()
        
        try:
            # Build default prompt if not provided
            if prompt is None:
                if self.use_nvidia:
                    # NVIDIA: Use JSON format instruction with type hints
                    prompt = f"""
                    Extract structured information from the following content.
                    Return the data as a JSON object with these fields and types: {self._get_schema_fields(schema)}.
                    
                    IMPORTANT TYPE REQUIREMENTS:
                    - Integer fields: Return only numbers (e.g., 1991, 100000)
                    - String fields: Return text (e.g., "Chicago", "alternative rock")
                    - List fields: Return arrays (e.g., ["rock", "pop"])
                    - If information is not available, use null (not "null" string, not "N/A")
                    
                    Only include information that is explicitly stated or strongly implied in the content.
                    For numbers, extract only the numeric value, not text descriptions.
                    
                    Content:
                    {content}
                    
                    Return only valid JSON, no markdown, no explanations.
                    """
                else:
                    # OpenAI: Use Instructor's default prompt
                    prompt = f"""
                    Extract structured information from the following content.
                    Return the data in the specified schema format.
                    Only include information that is explicitly stated or strongly implied in the content.
                    If information is not available, leave the field as null.
                    
                    Content:
                    {content}
                    """
            
            # Perform extraction
            if self.use_nvidia:
                # Use standard OpenAI client for NVIDIA (no Instructor)
                result = self._extract_without_instructor(content, schema, prompt, max_retries)
            else:
                # Use Instructor for OpenAI
                result = self._instructor_client.chat.completions.create(
                    model=self.model.value,
                    response_model=schema,
                    messages=[{"role": "user", "content": prompt}],
                    max_retries=max_retries
                )
            
            # Calculate metrics
            extraction_time = (datetime.utcnow() - start_time).total_seconds()
            
            # Estimate tokens
            estimated_tokens = len(content.split()) + len(str(result).split())
            cost = self._estimate_cost(estimated_tokens, self.model)
            
            # Update cost tracker
            self._cost_tracker['total_tokens'] += estimated_tokens
            self._cost_tracker['total_cost'] += cost
            self._cost_tracker['extraction_count'] += 1
            
            extraction_result = ExtractionResult(
                success=True,
                data=result,
                error=None,
                model_used=self.model.value,
                tokens_used=estimated_tokens,
                cost_estimate=cost,
                extraction_time=extraction_time,
                metadata={
                    'schema': schema.__name__,
                    'retries': max_retries
                }
            )
            
            logger.info(f"Extraction successful: {schema.__name__}, cost=${cost:.4f}")
            return extraction_result
            
        except Exception as e:
            extraction_time = (datetime.utcnow() - start_time).total_seconds()
            
            logger.error(f"Extraction failed: {e}")
            
            return ExtractionResult(
                success=False,
                data=None,
                error=str(e),
                model_used=self.model.value,
                tokens_used=0,
                cost_estimate=0.0,
                extraction_time=extraction_time,
                metadata={'schema': schema.__name__}
            )
    
    def extract_artist(self, content: str, prompt: Optional[str] = None) -> ExtractionResult:
        """Extract artist information"""
        if prompt is None:
            prompt = f"""
            Extract artist information from the following content.
            Include genres, origin, formation details, and any other relevant information.
            
            Content:
            {content}
            """
        return self.extract(content, ArtistExtraction, prompt)
    
    def extract_festival(self, content: str, prompt: Optional[str] = None) -> ExtractionResult:
        """Extract festival information"""
        if prompt is None:
            prompt = f"""
            Extract festival information from the following content.
            Include location, genres, capacity, timing, and any other relevant details.
            
            Content:
            {content}
            """
        return self.extract(content, FestivalExtraction, prompt)
    
    def extract_lineup_appearance(self, content: str, prompt: Optional[str] = None) -> ExtractionResult:
        """Extract lineup appearance information"""
        if prompt is None:
            prompt = f"""
            Extract lineup appearance information from the following content.
            Include artist name, festival, year, stage, day, and position if available.
            
            Content:
            {content}
            """
        return self.extract(content, LineupAppearance, prompt)
    
    def extract_agency_relationship(self, content: str, prompt: Optional[str] = None) -> ExtractionResult:
        """Extract agency relationship information"""
        if prompt is None:
            prompt = f"""
            Extract agency/management relationship information from the following content.
            Include artist name, organization name, relationship type, territory, and exclusivity.
            
            Content:
            {content}
            """
        return self.extract(content, AgencyRelationship, prompt)
    
    def extract_venue(self, content: str, prompt: Optional[str] = None) -> ExtractionResult:
        """Extract venue information"""
        if prompt is None:
            prompt = f"""
            Extract venue information from the following content.
            Include name, location, capacity, type, and address if available.
            
            Content:
            {content}
            """
        return self.extract(content, VenueExtraction, prompt)
    
    def extract_contact(self, content: str, prompt: Optional[str] = None) -> ExtractionResult:
        """Extract contact information"""
        if prompt is None:
            prompt = f"""
            Extract contact information from the following content.
            Include name, role, company, email, and phone if available.
            
            Content:
            {content}
            """
        return self.extract(content, ContactExtraction, prompt)
    
    def extract_batch(self, 
                      contents: List[str],
                      schema: Type[T],
                      prompt: Optional[str] = None) -> List[ExtractionResult]:
        """
        Extract from multiple contents in batch
        
        Args:
            contents: List of contents to extract from
            schema: Pydantic schema
            prompt: Optional custom prompt
            
        Returns:
            List of extraction results
        """
        results = []
        
        for content in contents:
            result = self.extract(content, schema, prompt)
            results.append(result)
        
        logger.info(f"Batch extraction complete: {len(results)} items")
        return results
    
    def get_cost_metrics(self) -> Dict[str, Any]:
        """Get cost tracking metrics"""
        return {
            'total_tokens': self._cost_tracker['total_tokens'],
            'total_cost': self._cost_tracker['total_cost'],
            'extraction_count': self._cost_tracker['extraction_count'],
            'average_cost_per_extraction': (
                self._cost_tracker['total_cost'] / self._cost_tracker['extraction_count']
                if self._cost_tracker['extraction_count'] > 0 else 0
            ),
            'model': self.model.value
        }
    
    def reset_cost_tracker(self):
        """Reset cost tracking"""
        self._cost_tracker = {
            'total_tokens': 0,
            'total_cost': 0.0,
            'extraction_count': 0
        }
        logger.info("Cost tracker reset")


def create_llm_extractor(api_key: str, 
                         model: ExtractionModel = ExtractionModel.GPT4O_MINI,
                         base_url: Optional[str] = None) -> LLMExtractor:
    """
    Factory function to create LLM extractor
    
    Args:
        api_key: OpenAI API key
        model: Model to use
        base_url: Optional custom base URL
        
    Returns:
        LLMExtractor instance
    """
    return LLMExtractor(api_key, model, base_url)
