# PokeProxy

A reverse proxy service that receives Pokemon data streams as protobuf-encoded payloads, validates HMAC signatures, matches against configurable routing rules, and forwards matching Pokemon as JSON to downstream services. It includes a Redis caching layer to avoid re-processing previously seen payloads.

## How It Works

```
[Client] --POST protobuf+HMAC--> [PokeProxy /stream]
                                       |
                                  1. Validate HMAC signature
                                  2. Check Redis cache
                                  3. Decode protobuf (on cache miss)
                                  4. Match against routing rules
                                  5. Convert to JSON
                                  6. Forward to downstream
                                       |
                                  [Downstream Service]
```

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Redis server running locally

## Quick Start

```bash
# Install dependencies
uv sync --dev

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Start Redis (if not already running)
redis-server &

# Start the mock downstream service
uv run uvicorn mock_service.main:app --host 127.0.0.1 --port 8001 &

# Start PokeProxy
uv run uvicorn pokeproxy.main:app --host 127.0.0.1 --port 8000
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POKEPROXY_HMAC_KEY` | Yes | - | Base64-encoded HMAC key. Must decode to at least 16 bytes; the service refuses to start otherwise. Generate with `openssl rand -base64 32` |
| `POKEPROXY_CONFIG` | Yes | - | Path to rules JSON file |
| `POKEPROXY_PORT` | No | 8000 | Proxy service port |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL |
| `LOG_LEVEL` | No | `INFO` | Log level. Output is always JSON on stdout, one object per line |
| `FORWARD_MAX_ATTEMPTS` | No | `3` | Max attempts forwarding to a downstream before giving up |
| `FORWARD_DEADLINE_SECONDS` | No | `10.0` | Wall-clock budget across all forward attempts combined |
| `REDIS_CONNECT_TIMEOUT_SECONDS` | No | `2.0` | Timeout establishing a connection to Redis |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | No | `2.0` | Timeout for a single Redis read/write |

### Rules Config

Rules are loaded from the JSON file specified by `POKEPROXY_CONFIG`.

```json
{
  "rules": [
    {
      "url": "http://localhost:8001/pokemon",
      "reason": "strong fire pokemon",
      "match": ["type_one==Fire", "attack>80", "generation<4"]
    }
  ]
}
```

**Match operators:** `==`, `!=`, `>`, `<`

**Match logic:** All conditions in a rule must match (AND). First matching rule wins.

**Fields:** `number`, `name`, `type_one`, `type_two`, `total`, `hit_points`, `attack`, `defense`, `special_attack`, `special_defense`, `speed`, `generation`, `legendary`

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stream` | POST | Proxy endpoint — validates, matches, forwards |
| `/health` | GET | Health check |
| `/stats` | GET | Per-endpoint metrics |

### Logging and request correlation

Logs are JSON, one object per line, on stdout. Every request produces exactly
one access line carrying `request_id`, `method`, `path`, `status`,
`duration_ms` and `outcome`.

`outcome` is the reason a request ended the way it did, and is the field worth
alerting on — several outcomes share a status code. Current values:
`forwarded`, `no_rule_matched`, `rejected_signature_missing`,
`rejected_signature_invalid`, `rejected_protobuf`, `rejected_too_large`,
`downstream_timeout`, `downstream_error`, `internal_error`.

`no_rule_matched` returns HTTP 200 with an empty body, so it is only
distinguishable from a successful forward by this field.

Requests are correlated with `X-Request-ID`. Supply one and it is echoed back
and passed downstream; omit it and the proxy generates a UUID4. `/health` and
`/stats` are not access-logged.

## Load Generator

A load generator script is included to send synthetic Pokemon traffic:

```bash
cd app
uv run python scripts/load_generator.py --rps 10 --duration 60
```

Options:
- `--url` — Target URL (default: `http://localhost:8000/stream`)
- `--rps` — Requests per second (default: 10)
- `--duration` — Duration in seconds, 0 for infinite (default: 60)
- `--secret` — Base64-encoded HMAC secret (default: test secret)

## Testing

```bash
uv run pytest -v
```
