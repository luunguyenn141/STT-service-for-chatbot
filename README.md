# Vietnamese Speech-to-Text Microservice for Banking Chatbot

A high-performance Vietnamese Speech-to-Text (STT) microservice built with FastAPI and powered by **ElevenLabs Scribe v2**, designed to integrate seamlessly into a Personal Financial Management (PFM) chatbot for banking mobile apps.

It provides a secure, low-latency REST API that converts customer voice recordings into clean text that conversational AI agents can understand and process.

---

## Architecture & Integration Flow

```mermaid
flowchart LR
    User["📱 Mobile App User"] -->|Records voice| MobileApp["Bank Mobile App"]
    MobileApp -->|Voice audio + User Session| Chatbot["🤖 PFM Chatbot Backend\n(LLM / Agent)"]
    subgraph STTMicroservice["🎙️ Vietnamese STT Microservice"]
        API["POST /api/v1/transcriptions\n(Auth + Rate Limit + Audit)"]
        Builder["Provider Adapter"]
        EL["ElevenLabs Scribe v2\n(Cloud API + Keyterm Biasing)"]
        API --> Builder
        Builder -->|STT_PROVIDER=elevenlabs| EL
    end
    Chatbot -->|POST audio + Bearer Token| API
    EL -->|Fast cloud transcription| API
    API -->|{ text: 'Chuyển 500k cho Nam' }| Chatbot
    Chatbot -->|Executes intent & responds| MobileApp
```

---

## Why ElevenLabs Scribe v2 for Banking PFM?

1. **High Vietnamese Accuracy**:
   - Handles accents, natural speaking pace, colloquial terms, and capitalization correctly out-of-the-box.
2. **Domain Keyterm Prompting (`STT_KEYTERMS`)**:
   - Injects domain-specific banking terms (e.g. `MSB, thẻ tín dụng, chuyển khoản, số dư, tiết kiệm, sao kê`) directly into the model's decoding context, virtually eliminating errors on banking terminology.
3. **No Heavy GPU Infrastructure Required**:
   - Runs as a lightweight container (~150MB image) without requiring CUDA drivers or massive PyTorch/Transformers dependencies on your server.
4. **Rich Metadata**:
   - Returns word-level timestamps, speaker separation, and confidence log probabilities in addition to the transcript text.

*(Note: VinAI PhoWhisper local adapter remains implemented as an optional fallback if on-prem inference is ever required).*

---

## Key Microservice Features

1. **Service-to-Service Authentication**:
   - Secure the API using a pre-shared token via `SERVICE_API_KEY`.
   - Supports `Authorization: Bearer <key>` or `x-api-key: <key>`.
   - Can be left unset for open local development.

2. **In-Memory Rate Limiting**:
   - Protects the service and manages cloud API quotas via `RATE_LIMIT_PER_MINUTE`.
   - Returns standard `429 Too Many Requests` with `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining` headers.

3. **Configurable CORS**:
   - Multi-origin support via `ALLOWED_ORIGINS` for chatbot admin consoles, webviews, and mobile test clients.
   - Preflight `OPTIONS` properly exposes required headers (`Authorization`, `x-api-key`, `x-request-id`).

4. **Banking Data Privacy & Audit Logging**:
   - Full audit trail logging: timestamps, client IP, provider, audio size, response code, and latency.
   - **Zero Content Logging**: Neither raw voice audio nor transcription text is ever stored or logged.

5. **Production Docker Deployment**:
   - Clean, lightweight Dockerfile running as a non-root user (`appuser`).
   - `docker-compose.yml` with health checks ready for containerized deployment.

---

## Quickstart

### 1. Run Locally with Python

```powershell
# Create & activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Configure environment
Copy-Item .env.example .env
```

Open `.env` and set your ElevenLabs API key:
```dotenv
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
STT_PROVIDER=elevenlabs
STT_MODEL_ID=scribe_v2
STT_KEYTERMS=MSB, thẻ tín dụng, chuyển khoản, số dư, tài khoản, tiết kiệm
SERVICE_API_KEY=your_secure_microservice_token
RATE_LIMIT_PER_MINUTE=60
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:3000
```

Start the service:
```powershell
uvicorn app.main:app --reload --port 8000
```

- Web UI: [http://localhost:8000](http://localhost:8000)
- OpenAPI Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)

---

### 2. Run with Docker Compose

```powershell
# Build and run
docker compose up -d

# View logs
docker compose logs -f

# Check health
curl http://localhost:8000/health
```

---

## API Reference

### 1. Health Check
```http
GET /health
```
Response:
```json
{
  "status": "ok",
  "provider": "elevenlabs",
  "configured": true
}
```

### 2. Speech Transcription
```http
POST /api/v1/transcriptions
Authorization: Bearer <SERVICE_API_KEY>
Content-Type: multipart/form-data
```

**Parameters:**
- `audio` (required): Audio file (`.wav`, `.mp3`, `.m4a`, `.ogg`, `.webm`, `.mp4`).
- `language` (optional): Vietnamese language code (defaults to `"vie"`).
- `keyterms` (optional): Additional per-request non-sensitive banking terms.

**Success Response (`200 OK`):**
```json
{
  "request_id": "84c8a2b5-e66b-4e4b-9fb3-cb517b620921",
  "text": "Tôi muốn kiểm tra số dư tài khoản thanh toán",
  "language_code": "vie",
  "language_probability": 0.99,
  "words": [
    {
      "text": "Tôi",
      "start": 0.04,
      "end": 0.22,
      "logprob": -0.05
    }
  ],
  "provider": "elevenlabs",
  "model": "scribe_v2"
}
```

**Chatbot Agent Usage:**
The conversational agent only needs `response["text"]` to feed into intent classification or LLM prompt templates.

---

## Chatbot Agent Integration Examples

### Python (Chatbot Backend using `httpx`)

```python
import httpx

async def transcribe_voice_message(audio_bytes: bytes, filename: str = "voice.wav") -> str:
    url = "http://stt-service:8000/api/v1/transcriptions"
    headers = {"Authorization": "Bearer your_secure_microservice_token"}
    files = {"audio": (filename, audio_bytes, "audio/wav")}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, files=files)
        response.raise_for_status()
        data = response.json()
        return data["text"]  # E.g. "Tôi muốn chuyển 500k cho Nam"
```

### Node.js / TypeScript (Chatbot Backend using `axios`)

```typescript
import axios from 'axios';
import FormData from 'form-data';

async function transcribeAudio(audioBuffer: Buffer): Promise<string> {
  const form = new FormData();
  form.append('audio', audioBuffer, { filename: 'voice.wav', contentType: 'audio/wav' });

  const response = await axios.post('http://stt-service:8000/api/v1/transcriptions', form, {
    headers: {
      ...form.getHeaders(),
      Authorization: 'Bearer your_secure_microservice_token',
    },
  });

  return response.data.text;
}
```

---

## Testing

Run unit & integration tests (runs in < 1 second with no external dependencies or real API keys needed):

```powershell
pytest
```