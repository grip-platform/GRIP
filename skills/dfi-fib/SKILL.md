---
name: dfi-fib
description: DFI-FIB = Direct-First Improvement with Fibonacci depth control. The recursive self-improvement kernel at GRIP's converge core. See canonical references below.
status: Active
---

# DFI-FIB

**DFI-FIB** stands for **Direct-First Improvement with Fibonacci depth control**. It is the recursive self-improvement kernel at the heart of GRIP's converge system.

Canonical references:
- `docs/DFI-FIB.md` — operator guide (definition, usage, fixed-point semantics, telemetry)
- `lib/improve_kernel.py` — implementation (`InnerspeakerGo` class)
- `plans/dfi-fibonacci-kernel.md` — original design doc

Earlier iterations of this skill (commits c0eeb7cb + 420ff6b3) fabricated a different expansion ("Dispatch-Fanout-Iterate"); reverted on advice of the broly mesh council on 2026-04-27. The orchestrator-only-loop dispatch pattern that motivated those commits has moved to `skills/bg-dispatch/`.
