# teslasolar/AudioFabric — Voice-Driven Audio-Visual Platform

Static HTML/JS platform hosted on GitHub Pages. Voice-controlled music generation,
AI arenas, hash visualisations, and ISA-95 enterprise hierarchy.

## Architecture

No backend (except `dev-agent/` Express for WebLLM). Pure ES Modules loaded via
`<script type="module">`. Hash-based SPA routing.

## Key Directories

| Directory | Purpose |
|-----------|---------|
| `modules/` | ~95 ES modules (core, voice, synths, effects) |
| `arena/` | KI Arena variants (battle, royale, ultra) |
| `combat/` | PvP, duels, voice battles |
| `enterprise/` | ISA-95 L0-L4 hierarchy |
| `fold-hash/` | Hash visualisations (3D, terrain, galaxy) |
| `gaia/` | Environmental audio (aurora, breath, pulse) |
| `ai/` | WebLLM sandbox and arena |

## Running

Static files — serve with any HTTP server:
```bash
npx serve .
# Or open index.html directly
```

## Conventions

- Vanilla JS with ES Modules (no build step)
- Web Audio API for all sound generation
- Voice recognition via Web Speech API
- ISA-95 enterprise model in `enterprise/`

## Novel Features

- Voice-controlled music composition via Web Speech API
- KI Arena: competitive AI-vs-AI music battles
- Fold-hash visualisations: cryptographic hash functions as 3D art
- ISA-95 equipment model applied to audio processing pipeline
