# teslasolar/suno — Konomi Suno API

TypeScript Express proxy for Suno's music generation API.

## Architecture

Express server (port 3456) proxying to `studio-api.suno.ai` via Clerk JWT auth.
ISA-95 tag system for equipment monitoring (SessionMgr, GenEngine, AssetMgr).

## Key Files

| File | Purpose |
|------|---------|
| `src/index.ts` | Express routes, startup, tag init |
| `src/suno.ts` | SunoClient: Clerk auth, JWT refresh, studio-api proxy |
| `src/tags.ts` | TagStore: ISA-95 equipment hierarchy (EventEmitter) |
| `src/cookie.ts` | Cookie extraction from browser SQLite DBs |
| `app/js/*.js` | SPA frontend (hash-based router) |

## Running

```bash
npm install
npx tsx src/index.ts
# Server starts on http://localhost:3456
```

## Auth Setup

1. Extract cookie automatically: `POST /auth/extract {"browser":"chrome"}`
2. Or set manually: `POST /auth/cookie {"cookie":"__client=..."}`
3. JWT auto-refreshes every 30 seconds

## Conventions

- TypeScript with tsx (no compile step)
- ISA-95 equipment states: 0=idle, 1=starting, 2=standby, 3=running, 4=busy, 7=fault
- Model: chirp-v4, 10 credits per generation, 2 clips per request

## Testing

```bash
npx tsx test/run.ts  # 28 tests
```
