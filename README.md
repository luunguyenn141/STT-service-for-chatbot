# Vietnamese Speech-to-Text POC

A small browser-testable proof of concept that sends Vietnamese audio to the
ElevenLabs Speech-to-Text API and displays the transcript. It is deliberately
limited to transcription: it does **not** execute banking actions, save audio,
or manage users.

## What it does

```text
Browser recording or audio upload
  → FastAPI backend
  → ElevenLabs Scribe v2
  → Vietnamese transcript in the browser
```

The vendor API key stays on the server. The browser never receives it.

## Prerequisites

- Python 3.11 or newer
- An ElevenLabs account and API key

The app starts without an API key so automated tests can run. Real
transcription returns a clear configuration message until a key is added.

## Run locally (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and set:

```dotenv
ELEVENLABS_API_KEY=your_key_here
```

Then start the application:

```powershell
uvicorn app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) to record a short
Vietnamese sentence or upload an audio file. Interactive API documentation is
available at [http://localhost:8000/docs](http://localhost:8000/docs).

For a command-line test:

```powershell
curl.exe -X POST http://localhost:8000/api/v1/transcriptions `
  -F "audio=@sample.wav"
```

## API

### `GET /health`

Shows whether the service is running and whether the provider key is present.
It never returns the key.

### `POST /api/v1/transcriptions`

Send `multipart/form-data` with:

| Field | Required | Description |
|---|---:|---|
| `audio` | Yes | WAV, MP3, M4A, WebM, OGG, or MP4 audio file |
| `language` | No | Vietnamese only; defaults to `vie` |
| `keyterms` | No | Comma-separated non-sensitive terms |

Successful response example:

```json
{
  "request_id": "uuid",
  "text": "Tôi muốn kiểm tra số dư tài khoản",
  "language_code": "vie",
  "language_probability": 0.99,
  "words": [],
  "provider": "elevenlabs",
  "model": "scribe_v2"
}
```

## Tests

Run automated tests without any external API key:

```powershell
pytest
```

They mock the vendor request and verify API validation, safe error mapping, and
the ElevenLabs multipart request format.

## Privacy and POC limits

- Use only non-sensitive demo recordings.
- Do not record account numbers, card details, passwords, or customer data.
- The application does not intentionally store audio or transcripts.
- Application logs contain request metadata only, not transcript content.
- Keyterm prompting is optional and may add vendor cost.
- Real banking use needs a vendor privacy/compliance review, authentication,
  confirmation of amounts and account identifiers, rate limiting, audit logs,
  and monitoring.

## Switching vendor later

Vendor-specific code is isolated in `app/services/stt/elevenlabs.py`. Add a
provider implementing `STTProvider` from `app/services/stt/base.py`, then
choose it through configuration without changing the public transcription API.
