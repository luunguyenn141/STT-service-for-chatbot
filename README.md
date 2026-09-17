# Vietnamese Speech-to-Text Microservice for Banking Chatbot

A Vietnamese Speech-to-Text (STT) microservice built with FastAPI and pluggable **Vbee**, **ElevenLabs**, and **PhoWhisper** providers, designed to integrate with a Personal Financial Management (PFM) chatbot for banking mobile apps.

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
        VB["Vbee STT\n(Vietnamese Cloud API)"]
        API --> Builder
        Builder -->|STT_PROVIDER=elevenlabs| EL
        Builder -->|STT_PROVIDER=vbee| VB
    end
    Chatbot -->|POST audio + Bearer Token| API
    EL -->|Fast cloud transcription| API
    VB -->|Vietnamese transcript| API
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

## Test with Vbee STT

Create an application at [Vbee Studio](https://studio.vbee.vn/apps), then configure the service:

```dotenv
STT_PROVIDER=vbee
VBEE_API_TOKEN=replace_with_the_vbee_jwt
VBEE_APP_ID=replace_with_the_vbee_app_id
REQUEST_TIMEOUT_SECONDS=120
SERVICE_API_KEY=replace_with_a_strong_service_secret
```

The public microservice endpoint stays `POST /api/v1/transcriptions`. The adapter:

- converts supported uploads to mono WAV at 16 kHz because Vbee STT accepts WAV;
- uses Vbee sync mode for audio shorter than 10 seconds;
- uses async mode and polls the transcript endpoint every 2 seconds for longer audio;
- keeps Vbee credentials on the server and never returns them to callers.

Vbee currently documents utterance timestamps rather than word timestamps, so
the normalized `words` array is empty for this provider. The request `keyterms`
field is accepted by this service but ignored because the Vbee STT API does not
document a keyterm parameter.

Run a lightweight Vbee image locally:

```powershell
docker build --build-arg DEFAULT_STT_PROVIDER=vbee -t msb-stt:vbee .
docker run --rm -p 18080:8080 --env-file .env msb-stt:vbee
```

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
curl http://localhost:8080/health
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

## Run PhoWhisper without a first-request download

Bundle the pinned `vinai/PhoWhisper-base` model into the image at build time:

```powershell
docker build --build-arg INSTALL_PHOWHISPER=true --build-arg BUNDLE_PHOWHISPER_MODEL=true -t msb-stt:bundled .
docker run --rm -p 18080:8080 --env-file .env -e STT_PROVIDER=phowhisper -e PHOWHISPER_DEVICE=-1 -e PHOWHISPER_PRELOAD=true -e HF_HUB_OFFLINE=1 msb-stt:bundled
```

Open `http://localhost:18080` after the application reports startup complete.
The image includes about 295 MB of model files under `/opt/models/phowhisper`,
outside the optional writable Hugging Face cache volume. The API continues to
report the public model ID. A different `PHOWHISPER_MODEL_ID` uses normal Hub
loading and requires its own cached model when offline mode is enabled.

`PHOWHISPER_PRELOAD=true` loads weights into memory before the server accepts
requests. Startup still takes a few seconds, and a new host must pull the larger
image, but the first user request no longer downloads weights. The model revision
can be changed explicitly with the `PHOWHISPER_BUNDLE_REVISION` build argument.
The bundle option defaults to `false` for existing lightweight builds.

For AgentBase, deploy the bundled image with `STT_PROVIDER=phowhisper`,
`PHOWHISPER_MODEL_ID=vinai/PhoWhisper-base`, `PHOWHISPER_DEVICE=-1`,
`PHOWHISPER_PRELOAD=true`, and `HF_HUB_OFFLINE=1`. Allow startup health checks
enough time for imports and loading the model into memory.

## Chatbot Agent Integration Examples

### Python (Chatbot Backend using `httpx`)

```python
import httpx

async def transcribe_voice_message(audio_bytes: bytes, filename: str = "voice.wav") -> str:
    url = "http://stt-service:8080/api/v1/transcriptions"
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

  const response = await axios.post('http://stt-service:8080/api/v1/transcriptions', form, {
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

---

## CI/CD: publish images to GreenNode VCR

The GitHub Actions workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
tests Python 3.11 and 3.12 before building two isolated Linux AMD64 images:

- `stt-service`: the existing image with PhoWhisper dependencies and bundled model;
- `stt-service-vbee`: a smaller image whose default provider is Vbee and whose credentials are supplied only at runtime.

| Event | Automated behavior |
| --- | --- |
| PR opened, updated, or reopened targeting `main` | Run tests and build both images; no registry login or image push. Fork PRs do not need registry secrets. |
| Push to `main`, including a merged PR | Run tests, build, and push both images to VCR with `latest` and the full Git commit SHA as tags. |
| Actions **Run workflow** on `main` | Run the same test/build/publish pipeline manually. Other branches only validate. |

### One-time GitHub and VCR setup

1. In the **STT repository**, open **Settings > Secrets and variables > Actions**
   and add repository secrets `VCR_USERNAME` and `VCR_PASSWORD`. Use a VCR
   repository user with push access to the destination repository. PFM uses these
   same secret names, but its GitHub repository secrets are not automatically
   available to STT. Never commit the credentials.
2. The existing image is `vcr.vngcloud.vn/111480-abp114564/stt-service`, using
   PFM's VCR repository and a separate image name. Confirm that destination is
   correct and the repository user can push there. To change it, set the Actions
   **variable** `VCR_IMAGE` to `vcr.vngcloud.vn/<repository>/<image>` without a
   tag or digest. The Vbee trial image defaults to
   `vcr.vngcloud.vn/111480-abp114564/stt-service-vbee`; override it with the
   **variable** `VCR_VBEE_IMAGE` using the same format. VCR uses the full image path for Docker pushes; see the
   [VNG image management documentation](https://docs.vngcloud.vn/vng-cloud-document/vcontainer-registry/repository/manage-image).
3. The default build uses CPU PyTorch for AgentBase. For an NVIDIA GPU vServer,
   set the optional Actions variable `STT_PYTORCH_INDEX_URL` to
   `https://download.pytorch.org/whl/cu126` and configure GPU access at runtime.
   Changing this variable changes the contents of the same image tags; use a
   separate `VCR_IMAGE` if you need to retain separate CPU and GPU images.
4. Commit the workflow together with all required image sources, including
   `Dockerfile` and `deploy/bundle_phowhisper.py`. The bundling script must be
   tracked in Git; a file present only on a developer's machine will not exist
   in the Actions checkout. Push to `main` or merge the PR, then check
   **Actions > CI/CD Pipeline** for the published image.

The build downloads dependencies and the pinned public Hugging Face model, so
the first build needs internet access and takes longer. Later builds reuse the
GitHub Actions Docker layer cache. A failed test or build prevents publishing.

### Use the published image

For the Vbee trial, deploy
`vcr.vngcloud.vn/111480-abp114564/stt-service-vbee:latest`, or preferably its
full commit SHA tag, and configure:

```dotenv
STT_PROVIDER=vbee
VBEE_API_TOKEN=replace_with_the_vbee_jwt
VBEE_APP_ID=replace_with_the_vbee_app_id
REQUEST_TIMEOUT_SECONDS=120
SERVICE_API_KEY=replace_with_a_strong_secret
```

The Vbee image defaults to `STT_PROVIDER=vbee`; setting it explicitly in VNG
makes the deployment intent visible. Store `VBEE_API_TOKEN` as a runtime secret.
Do not add it to the Docker build, GitHub Actions variables, or the repository.

For PhoWhisper, configure the GreenNode/AgentBase service to pull
`vcr.vngcloud.vn/111480-abp114564/stt-service:latest` (or your `VCR_IMAGE` path).
For reproducible releases and rollback, select the full commit SHA tag instead.
Give the platform registry pull credentials if the VCR repository is private.

The bundled CPU image listens on port `8080`. Configure its runtime environment:

```dotenv
STT_PROVIDER=phowhisper
PHOWHISPER_MODEL_ID=vinai/PhoWhisper-base
PHOWHISPER_DEVICE=-1
PHOWHISPER_PRELOAD=true
HF_HUB_OFFLINE=1
SERVICE_API_KEY=replace_with_a_strong_secret
```

Allow enough startup time for model imports/loading before probing `/health`.
Use `PHOWHISPER_DEVICE=0` and expose a GPU for the CUDA image.

This workflow automatically **publishes images**, matching PFM's workflow.
It does not call a GreenNode/AgentBase redeployment API or restart a running
service. Configure the platform's image-update trigger if it supports one, or
redeploy with the new SHA tag. Publishing `latest` alone does not instruct an
already-running container to restart. The vServer Compose/deploy instructions
below still build from source on the server; they do not consume the CI image.

## Deploy on GreenNode

The GreenNode production profile runs `vinai/PhoWhisper-base` locally on an
NVIDIA GPU. Audio never leaves this service for transcription, and no
ElevenLabs account or API key is required. The dedicated Compose profile in
[`deploy/greennode`](deploy/greennode) gives the container one GPU, persists the
Hugging Face model cache, publishes FastAPI on host port 8080, and provisions
an additional HTTPS endpoint through Caddy on ports 80/443.

### 1. Provision the GreenNode resources

Create a GreenNode GPU vServer using an Ubuntu NVIDIA/CUDA image with a Floating
IP. An RTX 4090 (24 GB) is the cost-conscious GreenNode option for this
single-model inference service. Use at least 4 vCPU, 16 GB RAM, and a 50 GB boot
volume so the CUDA-enabled PyTorch image and model cache have enough room.
Attach a security group with these inbound rules:

| Protocol | Port | Source | Purpose |
| --- | ---: | --- | --- |
| TCP | 22 | Your administrator IP only | SSH |
| TCP | 80 | `0.0.0.0/0` | ACME challenge and HTTPS redirect |
| TCP | 443 | `0.0.0.0/0` | HTTPS API |
| UDP | 443 | `0.0.0.0/0` | HTTP/3 (optional) |
| TCP | 8080 | Your test-client IP `/32` | Direct HTTP API (testing only) |

The container listens on port 8080, matching the AgentBase runtime requirement.
For vServer deployments, restrict host port 8080 to trusted test-client IPs because it uses unencrypted HTTP; use the HTTPS
domain for production audio and credentials. Create a DNS `A` record such as
`stt.example.com` pointing to the Floating IP before starting Caddy.

### 2. Install and deploy

SSH to the vServer, then run:

```bash
git clone https://github.com/luunguyenn141/STT-service-for-chatbot.git
cd STT-service-for-chatbot
sudo bash ./deploy/greennode/bootstrap-ubuntu.sh
```

Log out and back in once so the Docker group takes effect, then:

```bash
cd STT-service-for-chatbot/deploy/greennode
cp .env.production.example .env.production
chmod 600 .env.production
openssl rand -hex 32
```

Put the generated token in `SERVICE_API_KEY`, set `DOMAIN` and `ACME_EMAIL`,
then deploy. The first run builds the CUDA-enabled image, downloads PhoWhisper,
and waits until the model is loaded before reporting success:

```bash
bash ./deploy.sh
curl https://YOUR_DOMAIN/health
curl http://GREENNODE_FLOATING_IP:8080/health
```

For subsequent releases:

```bash
git pull --ff-only
bash ./deploy/greennode/deploy.sh
```

Useful operations:

```bash
cd deploy/greennode
docker compose --env-file .env.production logs -f --tail=100
docker compose --env-file .env.production ps
docker compose --env-file .env.production restart stt-service
```

The populated `.env.production` is ignored by Git and must remain only on the
server. For a browser client, call the HTTPS endpoint and send
`Authorization: Bearer <SERVICE_API_KEY>`; CORS is automatically restricted to
the deployed domain.
