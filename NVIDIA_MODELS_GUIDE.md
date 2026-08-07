# NVIDIA API Models Guide for Festival Bloomberg

This document provides recommendations for using NVIDIA's free API models for the Festival Bloomberg implementation, including text extraction, structured data extraction, and image processing.

## Overview

NVIDIA provides **free access** to 100+ AI models through the NVIDIA Developer Program. All models are OpenAI-compatible and can be used with the same API format.

**Base URL:** `https://integrate.api.nvidia.com/v1`  
**Authentication:** Bearer token via `Authorization: Bearer $NVIDIA_API_KEY`  
**Cost:** FREE for NVIDIA Developer Program members

## Recommended Text Models for Festival Bloomberg

### 1. Nemotron 4 340B Instruct (Recommended Default)
**Model ID:** `nvidia/nemotron-4-340b-instruct`  
**Best For:** Highest quality structured data extraction, complex reasoning

**Why This Model:**
- **Highest quality model available** (340B parameters)
- Best reasoning and extraction accuracy
- Excellent for complex documents
- Strong multi-language support
- **Completely FREE for developers**
- No cost penalty for using the best model

**Use Cases:**
- All Festival Bloomberg extraction tasks (default)
- Complex lineup parsing
- Multi-entity extraction
- High-stakes extraction where accuracy is critical
- Multi-language festival data

**Example Usage:**
```python
from extraction import LLMExtractor

# Automatically uses Nemotron 340B (default)
extractor = LLMExtractor()
result = extractor.extract_artist(artist_bio_text)
```

---

### 2. Llama 3.1 8B Instruct
**Model ID:** `meta/llama-3.1-8b-instruct`  
**Best For:** Faster extraction when speed is critical

**Why This Model:**
- Excellent instruction following
- Good balance of speed and quality
- Strong reasoning capabilities
- Multilingual support
- FREE for developers

**Use Cases:**
- Real-time extraction where latency matters
- Batch processing of simple documents
- When faster response times are needed

**Example Usage:**
```python
from extraction import LLMExtractor, ExtractionModel

extractor = LLMExtractor(model=ExtractionModel.NVIDIA_LLAMA_3_1_8B)
result = extractor.extract_artist(artist_bio_text)
```

---

### 2. Mistral 7B Instruct
**Model ID:** `mistralai/mistral-7b-instruct-v0.3`  
**Best For:** Fast extraction, simple structured data

**Why This Model:**
- Very fast inference
- Good for simple extraction tasks
- Low latency
- Strong performance on short texts

**Use Cases:**
- Quick artist name extraction
- Simple genre classification
- Basic venue information extraction
- Real-time processing

---

### 3. Mixtral 8x7B Instruct
**Model ID:** `mistralai/mixtral-8x7b-instruct-v0.1`  
**Best For:** Complex extraction, multi-entity recognition

**Why This Model:**
- Mixture of Experts architecture
- Strong reasoning capabilities
- Better at handling complex documents
- Good for multi-step extraction

**Use Cases:**
- Complex lineup parsing
- Multi-artist extraction from single text
- Detailed festival information extraction
- Relationship extraction (artist-agency, etc.)

---

### 4. Nemotron 4 340B Instruct
**Model ID:** `nvidia/nemotron-4-340b-instruct`  
**Best For:** Highest quality extraction, complex reasoning

**Why This Model:**
- Largest available model (340B parameters)
- Best reasoning capabilities
- Highest accuracy for complex tasks
- Excellent for nuanced extraction

**Use Cases:**
- Complex document understanding
- High-stakes extraction where accuracy is critical
- Multi-language extraction
- Complex relationship mapping

**Note:** May have higher latency due to model size

---

## Recommended Image Models for Festival Bloomberg

### 1. NVIDIA Nemotron Parse 2.0
**Model ID:** `nvidia/NVIDIA-Nemotron-Parse-2.0`  
**Best For:** OCR from festival posters, documents, images

**Why This Model:**
- State-of-the-art document parsing
- Extracts text with bounding boxes
- Classifies document elements (titles, tables, charts)
- Multilingual OCR support
- Chart-to-table conversion

**Use Cases:**
- Extracting lineups from festival poster images
- OCR from PDF documents
- Parsing schedule images
- Extracting text from venue maps

**Example Usage:**
```python
import base64
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="your-nvidia-api-key"
)

with open("festival_poster.jpg", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode("utf-8")

response = client.chat.completions.create(
    model="nvidia/NVIDIA-Nemotron-Parse-2.0",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                }
            ]
        }
    ]
)
```

---

### 2. NeMo Retriever OCR
**Model ID:** `nvidia/nemoretriever-parse`  
**Best For:** General OCR, text extraction from images

**Why This Model:**
- Fast OCR processing
- Good accuracy for standard text
- Confidence scores for detections
- Bounding box information

**Use Cases:**
- Quick text extraction from images
- Document digitization
- Receipt/invoice processing
- Basic OCR tasks

---

## Model Selection Guide

### For Text Extraction

| Task | Recommended Model | Reason |
|------|------------------|---------|
| Artist bio extraction | Llama 3.1 8B | Good balance of quality/speed |
| Festival description | Llama 3.1 8B | Strong instruction following |
| Lineup parsing | Mixtral 8x7B | Better at multi-entity extraction |
| Contact extraction | Mistral 7B | Fast for simple structured data |
| Complex documents | Nemotron 4 340B | Highest accuracy |
| Real-time processing | Mistral 7B | Lowest latency |

### For Image Processing

| Task | Recommended Model | Reason |
|------|------------------|---------|
| Festival poster OCR | Nemotron Parse 2.0 | Best document understanding |
| Schedule images | Nemotron Parse 2.0 | Chart/table conversion |
| Basic OCR | NeMo Retriever OCR | Fast and accurate |
| Venue maps | Nemotron Parse 2.0 | Spatial understanding |
| PDF documents | Nemotron Parse 2.0 | Document structure parsing |

---

## Integration with Festival Bloomberg

### Automatic Model Selection

The LLM extractor now automatically selects the best model based on available API keys:

```python
from extraction import LLMExtractor

# Automatically uses NVIDIA Nemotron 340B (highest quality, FREE)
extractor = LLMExtractor()

# Explicitly use NVIDIA with specific model
extractor = LLMExtractor(use_nvidia=True, model=ExtractionModel.NVIDIA_LLAMA_3_1_8B)

# Explicitly use OpenAI
extractor = LLMExtractor(use_nvidia=False)
```

**Default Model:** NVIDIA Nemotron 4 340B Instruct
- Highest quality model available
- Best reasoning and extraction accuracy
- Completely FREE for NVIDIA Developer Program members
- No cost penalty for using the best model

### Cost Comparison

| Model | Cost per 1K tokens | Monthly Cost (10K extractions) |
|-------|-------------------|--------------------------------|
| NVIDIA Llama 3.1 8B | **FREE** | **$0** |
| NVIDIA Mixtral 8x7B | **FREE** | **$0** |
| NVIDIA Nemotron 340B | **FREE** | **$0** |
| OpenAI GPT-4o-mini | $0.00075 | ~$7.50 |
| OpenAI GPT-4o | $0.01 | ~$100 |

**Savings:** Using NVIDIA models saves $7.50-$100+ per month for typical usage.

---

## Performance Characteristics

### Latency (Approximate)

| Model | Average Response Time |
|-------|----------------------|
| Mistral 7B | 200-400ms |
| Llama 3.1 8B | 300-600ms |
| Mixtral 8x7B | 400-800ms |
| Nemotron 340B | 800-1500ms |

### Quality Rankings

For structured data extraction:
1. **Nemotron 4 340B** - Highest accuracy
2. **Mixtral 8x7B** - Excellent for complex tasks
3. **Llama 3.1 8B** - Great all-around performer
4. **Mistral 7B** - Good for simple tasks

---

## Rate Limits

NVIDIA Developer Program free tier includes:
- **Rate limits apply** to free-tier usage
- Typical limit: ~100-500 requests per minute (varies by model)
- For production: Consider NVIDIA AI Enterprise for higher limits

**Monitoring:**
```python
# Check rate limit headers
response = extractor.extract(content, schema)
print(f"Rate limit remaining: {response.metadata.get('rate_limit_remaining')}")
```

---

## Best Practices

### 1. Use Appropriate Model Size
- **Simple tasks:** Mistral 7B (fastest)
- **Standard tasks:** Llama 3.1 8B (balanced)
- **Complex tasks:** Mixtral 8x7B or Nemotron 340B (highest quality)

### 2. Implement Retry Logic
```python
max_retries = 3
for attempt in range(max_retries):
    try:
        result = extractor.extract(content, schema)
        break
    except Exception as e:
        if attempt == max_retries - 1:
            raise
        time.sleep(2 ** attempt)  # Exponential backoff
```

### 3. Cache Results
```python
import hashlib

def cache_key(content, schema):
    return hashlib.md5(f"{content}{schema.__name__}".encode()).hexdigest()

# Check cache before extraction
```

### 4. Batch Processing
```python
# Process multiple items efficiently
results = []
for content in content_batch:
    result = extractor.extract(content, schema)
    results.append(result)
```

### 5. Monitor Usage
```python
# Track token usage and costs
print(f"Total tokens: {extractor._cost_tracker['total_tokens']}")
print(f"Total cost: ${extractor._cost_tracker['total_cost']:.2f}")
```

---

## Troubleshooting

### Common Issues

**Issue:** `401 Unauthorized`
**Solution:** Verify NVIDIA_API_KEY is set correctly in `.env`

**Issue:** `429 Too Many Requests`
**Solution:** Implement rate limiting and exponential backoff

**Issue:** Slow response times
**Solution:** Switch to smaller model (Mistral 7B) for faster inference

**Issue:** Poor extraction quality
**Solution:** Switch to larger model (Nemotron 340B) for better accuracy

---

## Getting Started

### 1. Get NVIDIA API Key
```bash
# Visit https://build.nvidia.com/
# Sign up for NVIDIA Developer Program (FREE)
# Navigate to Settings > API Keys
# Generate API key
```

### 2. Configure Environment
```bash
# Run setup script
python scripts/setup_env.py

# Or manually add to .env
echo "NVIDIA_API_KEY=your-key-here" >> .env
```

### 3. Test Extraction
```python
from extraction import LLMExtractor

extractor = LLMExtractor()
result = extractor.extract_artist("Radiohead is an English rock band...")
print(result.data.name)  # Should output: Radiohead
```

---

## Additional Resources

- **NVIDIA API Catalog:** https://build.nvidia.com/
- **NVIDIA Documentation:** https://docs.api.nvidia.com/nim/docs/
- **Model Quickstart:** https://docs.api.nvidia.com/nim/docs/api-quickstart
- **Free Models List:** Enable "Free Endpoint" filter in catalog

---

## Summary

**Recommendation:** Use NVIDIA Llama 3.1 8B Instruct as the default model for Festival Bloomberg text extraction. It provides excellent quality, good speed, and is completely free.

**For Images:** Use NVIDIA Nemotron Parse 2.0 for OCR and document understanding from festival posters and schedules.

**Cost Savings:** Switching from OpenAI to NVIDIA saves $7.50-$100+ per month while maintaining or improving quality.
