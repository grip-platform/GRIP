---
name: genome-breeding
description: Cross-team genome crossover through portable dict-level breeding, with safety gates. Use when invoking /breed or combining genomes from different GRIP instances.
---

# Genome Breeding

Cross-team genome crossover via portable dict-level breeding.

## Triggers

- `/breed`
- "breed genomes"
- "crossover team"
- "genome breeding"

## Commands

| Command | Description |
|---------|-------------|
| `/breed` | Round-robin breed all operator genomes |
| `/breed export` | Export local genome to `data/genomes/{operator}.json` |
| `/breed import` | Import bred offspring into local genome |
| `/breed status` | Show breeding pool stats and overlap matrix |
| `/breed --pair A B` | Cross two specific operators |
| `/breed --strategy tournament` | Fitness-proportionate random pairing |
| `/breed --dry-run` | Preview without writing files |

## Protocol

1. Each operator exports their genome: `python3 lib/genome_breeder.py export`
2. Breed all genomes: `python3 lib/genome_breeder.py breed`
3. Import offspring: `python3 lib/genome_breeder.py import`
4. Evolve through RSI: `/rsi` or `/converge`
5. Re-export and repeat

## Breeding Strategies

- **round-robin** (default, N<=5): C(N,2) pairs, tournament select top K
- **tournament** (N>5): Fitness-proportionate random pairing
- **directed**: Explicit operator pairing via `--pair`

## Safety Gates

- KONO-permanent genes keep fitness floor of 0.6
- New genes enter at capped fitness (NEUTRAL=0.5), 5-gen decay immunity
- Local high-fitness genes never downgraded (max-fitness merge)
- Fingerprint dedup skips breeding when pool unchanged
- Sensitive fields stripped from all exports

## Architecture

```
data/genomes/alex.json   -- V>> genome (version 2)
data/genomes/sam.json   -- K>> genome (version 2)
data/genomes/_bred.json    -- Best offspring from last breed
data/genomes/_breed-meta.json -- Dedup fingerprint
```
