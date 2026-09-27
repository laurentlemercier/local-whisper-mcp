# local-whisper-mcp

[English](README.md) | [Français](README.fr.md)

Local speech-to-text **service**, in a single process: the FastAPI REST API and the MCP
Streamable HTTP server share the same `JobManager`, the same job queue and the same
Whisper model cache.

No cloud API calls. Audio is downloaded, transcribed, and the job metadata is kept on
local disk.

Copyright (C) 2026 Laurent Lemercier. Licensed under the
[GNU AGPL v3.0](LICENSE).

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Getting started](#getting-started)
- [REST API](#rest-api)
- [MCP tools](#mcp-tools)
- [Models](#models)
- [Supported audio formats](#supported-audio-formats)
- [Docker](#docker)
- [Tests](#tests)
- [Security](#security)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [License](#license)

---

## Features

- **Asynchronous submission**: every transcription returns a `job_id` immediately. The
  REST call responds `202 Accepted`; the client then polls for state.
- **Three audio sources**: remote URL, multipart upload, or Base64.
- **Two interfaces**: REST and MCP Streamable HTTP, on the same port, with no second
  process.
- **Webhooks**: when a job ends (success or failure), the service POSTs the job state to
  a callback URL.
- **Transcription metrics**: audio duration, processing duration, and real-time factor
  (`real_time_factor`).
- **SSRF protection**: download and callback URLs are validated, including on every
  redirect.
- **On-disk job persistence**, readable again after a restart.

## Requirements

- **Python 3.11** or later.
- **FFmpeg** is only required for the Docker containers. Locally, audio duration is
  determined by [PyAV](https://github.com/PyAV-Org/PyAV) (a bundled native library) and,
  as a fallback, by [Mutagen](https://mutagen.readthedocs.io/) — no external binary is
  installed.
- **Disk space**: model weights are downloaded on first use (roughly 500 MB for `small`,
  roughly 1.5 GB for `medium`).

## Installation

```bash
git clone https://github.com/laurentlemercier/local-whisper-mcp.git
cd local-whisper-mcp

python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Every variable is read from the **process environment** at startup (`app/config.py`).
The application **does not load a `.env` file**: the `.env.example` in this repository
serves as reference only and is not applied automatically.

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_TOKEN` | *(empty)* | Bearer token required by REST and MCP. **Empty means no authentication.** |
| `DATA_DIR` | `./data` | Data root: audio inputs and job state. |
| `HOST` | `0.0.0.0` | Listening interface. |
| `PORT` | `8000` | Listening port. |
| `WHISPER_DEVICE` | `cpu` | Device passed to `faster-whisper` (`cpu`, `cuda`, `auto`). |
| `WHISPER_COMPUTE_TYPE` | `int8` | Quantisation (`int8`, `float16`, `float32`). |
| `MAX_INPUT_SIZE` | `524288000` | Maximum audio file size, in bytes (500 MiB). |
| `HTTP_TIMEOUT` | `60` | Maximum time to download audio, in seconds. |
| `CALLBACK_TIMEOUT` | `30` | Maximum time for the callback POST, in seconds. |
| `ALLOW_PRIVATE_URLS` | `false` | Allow audio URLs pointing at private IP addresses. |
| `ALLOWED_URL_HOSTS` | *(empty)* | Comma-separated allow-list of hosts permitted for audio. |
| `ALLOW_PRIVATE_CALLBACKS` | `false` | Allow callback URLs pointing at private IP addresses. |
| `ALLOWED_CALLBACK_HOSTS` | *(empty)* | Comma-separated allow-list of hosts permitted for callbacks. |

## Getting started

```bash
# Set a token, then start
export API_TOKEN="a-long-random-token"       # Linux / macOS
$env:API_TOKEN="a-long-random-token"        # Windows PowerShell

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- REST: <http://localhost:8000>
- MCP: <http://localhost:8000/mcp>
- Interactive OpenAPI documentation: <http://localhost:8000/docs>

> **Why `uvicorn app.main:app` instead of `python -m app.run`?**
> `app/run.py` calls `mount_mcp(app)`, which mounts the MCP server on `/mcp` a second
> time even though `app/main.py` has already mounted it. The second mount is
> unreachable: it creates an unused session manager. Going through
> `uvicorn app.main:app` avoids the duplication. See
> [Known limitations](#known-limitations).

For development with automatic reload:

```bash
uvicorn app.main:app --reload
```

## REST API

Every route except `/health` requires the `Authorization: Bearer <API_TOKEN>` header.

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/health` | Readiness probe. **Unauthenticated.** |
| `GET` | `/v1/models` | Supported models and models currently loaded in memory. |
| `POST` | `/v1/transcriptions` | Submits an audio URL. Responds `202`. |
| `POST` | `/v1/transcriptions/upload` | Submits an audio file as `multipart/form-data`. Responds `202`. |
| `POST` | `/v1/transcriptions/data` | Submits Base64-encoded audio. Responds `202`. |
| `GET` | `/v1/transcriptions/{job_id}` | Full job state. |
| `GET` | `/v1/transcriptions/{job_id}/result` | Transcription and metrics. |
| `DELETE` | `/v1/transcriptions/{job_id}` | Deletes the job and its audio. |

### Submit a URL

```bash
curl -X POST http://localhost:8000/v1/transcriptions \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "source": { "type": "url", "url": "https://example.com/audio.m4a" },
        "model": "small",
        "language": "en"
      }'
```

```json
{ "id": "tr_3f9a1c2b8e7d4051", "status": "queued", "model": "small", ... }
```

### Submit a file

```bash
curl -X POST http://localhost:8000/v1/transcriptions/upload \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@./audio.m4a" \
  -F "model=small" \
  -F "language=en"
```

### Fetch the result

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:8000/v1/transcriptions/tr_3f9a1c2b8e7d4051/result
```

`409` while the job is unfinished, `500` if the job failed, `200` once `status` is
`completed`.

Possible states are `queued`, `downloading`, `running`, `completed` and `failed`.

### Job completion webhook

Add `callback_url` (and `callback_headers` if needed) to the request. Once the
transcription finishes, successfully or not, the service performs a JSON `POST` with
the job state. `callback_url` and `callback_headers` are excluded from the body sent.

## MCP tools

MCP Streamable HTTP server on `/mcp`, named `local-whisper-speech-to-text`.

| Tool | Purpose |
| --- | --- |
| `transcribe_url` | Submits an audio URL. |
| `transcribe_data` | Submits Base64-encoded audio. |
| `get_transcription_status` | Full job state. |
| `get_transcription_result` | Transcription and duration metrics. |
| `delete_transcription` | Deletes a job. |

Every tool takes an optional `api_token` parameter, to be filled with the same value as
`API_TOKEN`.

Example MCP client configuration:

```json
{
  "mcpServers": {
    "local-whisper": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer a-long-random-token" }
    }
  }
}
```

## Models

Two models are accepted: `small` (default) and `medium`.

They are loaded on demand at first use and kept in memory for the lifetime of the
process. Loading `medium` needs roughly three times the memory of `small`; on CPU,
`int8` is the right compromise. `language: "auto"` lets Whisper detect the language.

## Supported audio formats

`.m4a` `.mp3` `.wav` `.ogg` `.opus` `.webm` `.flac`

The extension is checked on receipt. For an upload, a name without a valid extension is
rejected; for a URL or Base64 payload, the extension is derived from the path and, failing
that, from the response MIME type.

## Docker

The build context is the repository root; the image only contains `requirements.txt` and
`app/` (see `.dockerignore`).

```bash
cp docker/.env.example docker/.env

# Fill in DATA_PATH, MODELS_PATH, CURRENT_UID and CURRENT_GID in docker/.env
docker compose -f docker/docker-compose.service.yml up -d
```

`docker/.env.example` documents the expected variables. In particular:

- `CURRENT_UID` / `CURRENT_GID` stop the container from writing to `DATA_PATH` as root.
- `MODELS_PATH` acts as a persistent HuggingFace cache, so weights are not re-downloaded
  on every recreate.

The image is based on `python:3.11-slim` pinned by digest, with `ffmpeg` installed. Its
entrypoint is `uvicorn app.main:app`, so it does not run the duplicate mount described
above.

## Tests

The tests are manual and require a running service as well as a local audio file.

```powershell
cd tests

.\test-speech-service-v2.3.ps1 `
    -ApiToken "a-long-random-token" `
    -AudioFile "C:\paths\to\my\audio.m4a"
```

The script chains: `/health` probe, Bearer authentication, REST upload, state polling
until completion, result retrieval, then discovery and invocation of the MCP tools. It
deletes the jobs it created, unless `-KeepJobs` is passed.

It also automatically creates a virtual environment in `tests/.venv` and installs the
official MCP SDK there — which is why that directory is ignored by git. Pass
`-PythonExe` to choose the interpreter, `-SkipMcp` or `-SkipRestUpload` to narrow the
scope. Without `-AudioFile`, the REST and MCP tests are skipped with a warning.

`tests/mcp_client.py` is a standalone MCP client, usable on its own:

```bash
python tests/mcp_client.py \
    --endpoint http://localhost:8000/mcp \
    --api-token "$API_TOKEN" \
    --audio-file ./audio.m4a \
    --model small \
    --language en
```

## Security

### Authentication

If `API_TOKEN` is **empty**, the service requires no authentication and checks none. Do
not expose it to a network in that state. REST uses the `Authorization: Bearer` header;
MCP tools expect the `api_token` parameter.

### SSRF protection

Download and callback URLs go through the same validator (`app/security.py`), which
rejects:

- any scheme other than `http` and `https`;
- credentials embedded in the URL (`https://user:pass@host/...`);
- hosts absent from the allow-list, when `ALLOWED_URL_HOSTS` is set;
- `localhost`;
- any IP address the host resolves to that is private, loopback, link-local, reserved,
  multicast or unspecified.

Redirects are followed **manually**, at most six times, and every target is revalidated
before being requested. Size is checked against the `Content-Length` header and then
against the body actually received.

To fetch audio from an internal network, set `ALLOW_PRIVATE_URLS=true` — and prefer a
host allow-list over that relaxation.

### Network exposure

The service provides neither TLS nor rate limiting. Put it behind a reverse proxy that
terminates TLS, and restrict submissions to trusted clients: transcription is expensive
in CPU time.

## Project layout

```
app/
  config.py     Environment variables and data directories
  models.py     Pydantic schemas (Job, Source, Result, Statuses)
  security.py   Bearer token and SSRF validation
  source.py     URL download, Base64, extension checking
  jobs.py       JobManager: queue, JSON persistence, locks
  whisper.py    Model cache and faster-whisper invocation
  worker.py     Processing thread: download, transcription, webhook
  mcp.py        MCP server and its five tools
  main.py       FastAPI application, REST routes, MCP mount
  run.py        Alternative entrypoint (see Known limitations)
data/
  input/        Downloaded or uploaded audio, one directory per job
  jobs/         State of each job as JSON
docker/         Dockerfile and compose file for a containerised deployment
tests/          PowerShell test script and Python MCP client
```

A job is laid out as follows:

```
data/jobs/tr_3f9a1c2b8e7d4051.json          state, metrics, result
data/input/tr_3f9a1c2b8e7d4051/audio.m4a    source audio
data/input/tr_3f9a1c2b8e7d4051/source.json  original URL, if remote source
```

## Known limitations

- **Single-threaded queue.** Only one job is transcribed at a time. The queue lives in
  memory: it does not survive a restart, and an in-flight job is lost.
- **Duplicate MCP mount.** `app/main.py:549` mounts the MCP server on `/mcp`, then
  `app/run.py:5` calls `mount_mcp(app)`, which mounts it again. Only the first mount is
  reachable; the second creates an orphaned session manager. Use
  `uvicorn app.main:app`.
- **Dead variables in Docker.** `MODEL` and `LANGUAGE` are set in the Dockerfile and the
  compose file, but the application never reads them: model and language are chosen per
  request. The `whisper_models` volume declared in the compose file is not used either.
- **No rate limiting or quota.** An authorised client can saturate the queue.
- **No automatic expiry.** Jobs and their audio occupy disk until explicitly deleted.
- **No CI.** The tests require a live service and a model weight download, which makes
  them a poor fit for continuous integration without a cache.

## License

GNU Affero General Public License v3.0. The full text is in [LICENSE](LICENSE).

AGPL v3.0 has a **network clause** (section 13): if you modify this software and make it
available to users who access it over a network, you must offer those users the source
code of your modified version. This is the essential difference from the classic GPL,
which only covers distribution.

Copyright (C) 2026 Laurent Lemercier.
