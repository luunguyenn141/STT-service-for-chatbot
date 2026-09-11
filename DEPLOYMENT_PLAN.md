# Cloud deployment remediation plan

Status: planning; implementation and cloud deployment have not started.

## Baseline

- Reviewed main at cf684e1ff484ae32a76eefbba32bcf4704651560 plus local changes.
- 23 tests pass; pip check passes; GreenNode Compose configuration validates.
- Docker daemon was unavailable; container build, GPU inference, TLS and load testing remain unverified.
- Existing local changes affect .env.example, Dockerfile, README.md, app/config.py, app/main.py and app/services/stt/phowhisper.py. deploy/ is untracked.
- Preserve those changes. Child checkouts start from committed HEAD, so every child must inspect the parent working files before proposing edits. Never copy .env, private keys, model caches or logs into a child or commit them.

## Child 1: API security and upload limits

Workspace name: cloud-api-security

Own app/api/transcriptions.py, app/services/rate_limiter.py, new upload/proxy middleware modules and focused security tests. Propose shared app/main.py and app/config.py edits in the plan for coordinated integration.

1. Define trusted-proxy handling. Ignore forwarded client addresses from untrusted peers; use a validated client address for audit logs and rate limiting. Configure the reverse proxy and ASGI server consistently.
2. Enforce an aggregate request-body limit while receiving bytes, before multipart parsing can spool an oversized file. Cover chunked requests and misleading or absent Content-Length. Coordinate a matching edge limit with child 3, allowing multipart overhead.
3. Bound rate-limiter state and expire inactive clients. Document process-local limits; a single-instance release does not require Redis, but multiple workers/replicas need shared limits or enforcement at the edge.
4. Define production fail-closed authentication without breaking explicit local-development mode.

Acceptance: spoofing X-Forwarded-For cannot reset a quota; trusted-proxy requests retain the correct identity; oversized and chunked uploads stop with 413 without filling temporary storage; valid supported uploads still pass; expired client entries are reclaimed; production rejects missing credentials.

## Child 2: Inference capacity and readiness

Workspace name: cloud-inference-readiness

Own app/services/stt/phowhisper.py, app/api/health.py, readiness models if needed, new inference coordinator modules and focused tests. Propose shared app/main.py and app/config.py edits for coordinated integration.

1. Add a bounded inference queue and explicit GPU concurrency, initially one active inference per GPU. Return a documented busy response when capacity is exhausted.
2. Define queue and inference deadlines. An asyncio timeout cannot terminate an already running inference thread: do not release its capacity slot while work is still running. Decide whether a worker process is needed for hard execution deadlines and recovery.
3. Bound decoded audio duration, not only compressed bytes. Verify supported formats and long-recording behavior against the selected model/runtime.
4. Separate liveness from readiness. Readiness must return 503 when required configuration or local model initialization is unavailable. Preserve a documented /health contract; avoid expensive external-provider calls on each probe.
5. Keep model initialization single-flight, define shutdown behavior and expose safe capacity/error diagnostics without audio or transcript content.

Acceptance: controlled tests prove concurrency and queue bounds, overload responses, timeout slot retention, startup failure readiness and shutdown behavior. Actual GPU validation remains a release gate; mocks do not establish GPU compatibility or Vietnamese accuracy.

## Child 3: Deployment and reproducible releases

Workspace name: cloud-deployment-release

Own Dockerfile, Docker Compose files, deploy/, dependency/lock files, .dockerignore, .github/workflows/ci.yml, .env.example and deployment documentation.

1. Incorporate the existing uncommitted GreenNode profile without overwriting its intended GPU, preloading or HTTPS behavior.
2. Remove the public HTTP mapping from production or bind it to loopback for explicit diagnostics. Document firewall rules and configure proxy trust/body limits to match child 1.
3. Exclude production environment files from Docker build context as well as Git. Validate required production secrets and provider settings.
4. Pin a verified Python/PyTorch/Transformers/CUDA dependency set and model revision; separate test dependencies from the production runtime where practical. Verify image compatibility rather than merely choosing newest versions.
5. Extend CI to cover CPU and GPU-image build paths and deployment syntax/configuration. Clearly separate builds from tests that require actual NVIDIA hardware.
6. Point deployment health checks at the readiness contract from child 2. Document logs, capacity metrics, alerts, resource budgets and restart/recovery behavior.
7. Define immutable release identification, rollback procedure and a target-server smoke/load test runbook. Release reviewed local changes so server clones contain them.

Acceptance: both image variants build; production config does not publicly expose plaintext HTTP; secrets are excluded; readiness gates startup; a clean checkout reproduces the release. GPU hardware, DNS/TLS and operational tests are explicitly recorded as passed or pending.

## Dependencies and integration order

1. All three children produce detailed plans in parallel, with file-level changes, tests and unresolved decisions. Planning only: do not implement, commit, push or deploy in this phase.
2. Before implementation, preserve the current parent changes as a reviewed baseline available to every child. Do not assume uncommitted files propagate through worktrees.
3. Agree on shared contracts: production-mode configuration, proxy trust, aggregate upload size, overload responses, readiness endpoint and shutdown behavior.
4. Implement API security and inference work independently. The parent integrator owns shared app/main.py and app/config.py wiring; avoid competing edits.
5. Integrate deployment changes after the shared endpoint/configuration contracts are settled.
6. Run the complete regression suite once integrated; build both container variants and test production Compose on a clean environment.
7. On the target GPU server, verify CUDA visibility, model download/cache persistence, real Vietnamese transcription, concurrent requests, overload and deadline behavior, readiness, restart, HTTPS and firewall exposure. Use non-sensitive test audio.
8. Record latency, throughput and peak RAM/VRAM against an agreed traffic target. Approve production only after all required checks pass and rollback is demonstrated.

## Definition of done

All reproduced security/resource issues have regression coverage; production images and configuration are reproducible; the target GPU deployment passes real inference and load checks; the exact tested revision is available for deployment; operational and rollback instructions are verified. No production-ready claim may rely only on the current 23 mocked/unit tests.
