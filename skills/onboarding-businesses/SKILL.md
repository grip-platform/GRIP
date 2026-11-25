---
name: onboarding-businesses
description: TUI wizard for voice assistant configuration using articulate-first pattern. Use when user invokes /onboard-business or wants to set up a new business voice assistant.
status: Active
effort: high
version: 2.0.0
triggers:
  - /onboard-business
  - "set up voice assistant"
  - "configure business assistant"
  - "onboard new business"
integrates:
  - reasoning-with-ai
  - creating-wizards
  - asking-users
  - hitl
---

# Onboarding Businesses

TUI wizard to configure a business voice assistant using the articulate-first pattern.

## Design Principles

```grip
; @asking-users PARAMOUNT compliance
wizard.step⟿ bidirectional-pattern (teach + collect)
wizard.checkpoint⟿ after.Step3,7,14,18 (Gate 5: multi-step)
wizard.confirm⟿ before.save (Gate 4: destructive)
wizard.list⟿ iterative ("Add item" / "Done (N added)")

; @creating-wizards anti-patterns
¬mention.Other ⫶ options.concrete ⫶ headers.≤12chars

; Articulate-first in TUI
phase.A⟿ present.thinking.directions (captures user_initial_view)
phase.B⟿ present.config.options (captures final_decision)
```

## Core Philosophy

The onboarding demonstrates the product's articulate-first pattern:

| Pattern | How Wizard Uses It |
|---------|-------------------|
| Articulate-first | Ask vision BEFORE showing config options |
| Options not recommendations | Present choices with trade-offs |
| Explicit reasoning | Capture WHY user made each choice |
| Two-phase per section | Phase A (thinking), Phase B (config) |

---

## Wizard Implementation

### Section 1: Business Identity (Steps 1-3)

#### Step 1: Business Vision (Articulate Phase A)

```text
Header: "Vision"
Question: "What kind of knowledge work will this assistant support?"
Options:
- "Team analysis" - Help teams analyse data, feedback, or performance
- "Professional support" - Assist with domain expertise (legal, medical, technical)
- "Decision support" - Help with complex decisions and trade-off analysis
- "Customer service" - Handle customer queries with domain knowledge

Teaching: User articulates vision BEFORE seeing config options
Collecting: business_vision (for reasoning.json)
```

**Processing:**
- Store selection in reasoning log as `user_initial_view`
- Move to config steps

#### Step 2: Business Name

```text
Header: "Name"
Question: "What's the name of your business or team?"
Options:
- "Type name" - Enter business/team name

Teaching: Config will be saved under this name
Collecting: business_name
```

**Note:** This is a text input via "Other" option pathway.

#### Step 3: Business Domain

```text
Header: "Domain"
Question: "What industry or domain does this serve?"
Options:
- "Technology" - Software, IT, engineering
- "Professional services" - Legal, consulting, accounting
- "Healthcare" - Medical, wellness, patient care
- "Finance" - Banking, insurance, investment

Teaching: Domain shapes assistant knowledge boundaries
Collecting: domain
```

**Checkpoint 1:** "Business identity configured. Next: Assistant persona."

---

### Section 2: Persona (Steps 4-7)

#### Step 4: Persona Vision (Articulate Phase A)

```text
Header: "Persona"
Question: "How should the assistant come across to users?"
Options:
- "Professional" - Formal, precise, business-appropriate
- "Friendly" - Warm, approachable, conversational
- "Technical" - Direct, detailed, assumes expertise
- "Supportive" - Encouraging, patient, explains thoroughly

Teaching: User defines personality before naming
Collecting: persona_vision (for reasoning.json)
```

#### Step 5: Assistant Name

```text
Header: "Name"
Question: "What should the assistant be called?"
Options:
- "GRIP" - The default GRIP identity
- "Custom name" - Enter a name that fits your brand

Teaching: Users will address the assistant by this name
Collecting: assistant_name
```

#### Step 6: Greeting Message

```text
Header: "Greeting"
Question: "How should the assistant greet users?"
Options:
- "Standard" - "Hi, I'm {name}. What would you like to think through?"
- "Formal" - "Hello. How may I assist you today?"
- "Casual" - "Hey! What's on your mind?"
- "Custom" - Enter your own greeting

Teaching: First impression sets the tone
Collecting: greeting
```

#### Step 7: Clarification Style

```text
Header: "Clarify"
Question: "When a request is unclear, how should clarification be sought?"
Options:
- "Direct" - "Do you mean X or Y?"
- "Gentle" - "I want to make sure I understand..."
- "Socratic" - "What aspects matter most to you?"

Teaching: Affects how assistant handles ambiguity
Collecting: clarification_style
```

**Checkpoint 2:** "Persona configured. Next: Knowledge domains."

---

### Section 3: Knowledge (Steps 8-11)

#### Step 8: Knowledge Vision (Articulate Phase A)

```text
Header: "Knowledge"
Question: "What should the assistant be knowledgeable about?"
Options:
- "Broad coverage" - General knowledge across your domain
- "Deep expertise" - Narrow focus with detailed knowledge
- "Process-focused" - Specific workflows and procedures
- "Mixed approach" - Combine general and specific knowledge

Teaching: User defines knowledge strategy before specifics
Collecting: knowledge_vision (for reasoning.json)
```

#### Step 9: Domains (List Collection)

```text
Header: "Domains"
Question: "Add a knowledge domain (e.g., 'contract analysis', 'code review')"
Options:
- "Add domain" - Type a domain to add
- [After 1+ added] "Done ({N} added)" - Proceed to next step

Teaching: These define what the assistant can help with
Collecting: domains[] (iterative)
```

**List Loop:** After each "Add domain", return to this step until "Done" selected.

#### Step 10: Boundaries (List Collection)

```text
Header: "Boundaries"
Question: "Add a topic the assistant should NOT handle (e.g., 'legal advice')"
Options:
- "Add boundary" - Type a boundary to add
- [After 1+ added] "Done ({N} added)" - Proceed to next step

Teaching: Clear boundaries prevent overreach
Collecting: boundaries[] (iterative)
```

#### Step 11: Sources (List Collection - Optional)

```text
Header: "Sources"
Question: "Add a reference source (e.g., 'company wiki') or skip"
Options:
- "Add source" - Type a source to add
- "Skip" - No sources configured
- [After 1+ added] "Done ({N} added)" - Proceed to next step

Teaching: Sources help ground responses
Collecting: sources[] (iterative, optional)
```

**Checkpoint 3:** "Knowledge configured. Next: Operations."

---

### Section 4: Operations (Steps 12-14)

#### Step 12: Escalation Vision (Articulate Phase A)

```text
Header: "Escalation"
Question: "When should the assistant hand off to a human?"
Options:
- "Conservative" - Escalate early, minimise AI risk
- "Balanced" - Standard triggers (complaints, explicit requests)
- "Confident" - Only escalate when truly necessary
- "Custom" - Define specific triggers

Teaching: User defines escalation philosophy
Collecting: escalation_vision (for reasoning.json)
```

#### Step 13: Escalation Triggers (List Collection)

```text
Header: "Triggers"
Question: "Add an escalation trigger (e.g., 'complaint detected', 'high stakes')"
Options:
- "Add trigger" - Type a trigger to add
- [After 1+ added] "Done ({N} added)" - Proceed to next step

Teaching: These situations route to human support
Collecting: escalation_triggers[] (iterative)
```

#### Step 14: Escalation Message

```text
Header: "Message"
Question: "What message should be shown when escalating?"
Options:
- "Standard" - "I'll connect you with someone who can help directly."
- "Apologetic" - "I'm not the right fit for this. Let me get someone better."
- "Informative" - "This requires human expertise. Connecting you now."
- "Custom" - Enter your own message

Teaching: Sets user expectations during handoff
Collecting: escalation_message
```

**Checkpoint 4:** "Operations configured. Next: Voice & accessibility."

---

### Section 5: Voice (Steps 15-18)

*Note: No articulate phase - these are technical configurations.*

#### Step 15: Language

```text
Header: "Language"
Question: "Primary language for the assistant?"
Options:
- "English" - en
- "Spanish" - es
- "French" - fr
- "German" - de

Teaching: Affects STT/TTS configuration
Collecting: language
```

#### Step 16: Speaking Pace

```text
Header: "Pace"
Question: "How fast should the assistant speak?"
Options:
- "Slow" - Deliberate, easy to follow - good for complex topics
- "Normal" - Natural conversational pace
- "Fast" - Efficient, assumes listener follows quickly

Teaching: Affects TTS speaking rate
Collecting: speaking_rate
```

#### Step 17: Voice ID

```text
Header: "Voice"
Question: "Configure ElevenLabs voice?"
Options:
- "Skip for now" - Configure voice ID later
- "Enter ID" - I have an ElevenLabs voice ID

Teaching: Browse voices at elevenlabs.io/voice-library
Collecting: voice_id
```

#### Step 18: Accessibility Mode

```text
Header: "Access"
Question: "Default accessibility mode?"
Options:
- "Default" - Voice input + voice & visual output
- "Deaf" - Text input + text & visual output
- "Blind" - Voice input + voice only (screen reader)
- "Noisy" - Text input + text output (no audio)

Teaching: Users can override per-session
Collecting: accessibility_mode
```

**Checkpoint 5:** "Voice configured. Next: Reasoning protocol."

---

### Section 6: Reasoning Protocol (Steps 19-23)

#### Step 19: Protocol Vision (Articulate Phase A)

```text
Header: "Protocol"
Question: "How much should the assistant guide vs defer to users?"
Options:
- "High guidance" - Structure thinking, require articulation
- "Balanced" - Some structure, flexible interaction
- "Low guidance" - Respond directly, user leads
- "Custom mix" - Configure each setting individually

Teaching: This defines the articulate-first protocol settings
Collecting: protocol_vision (for reasoning.json)
```

**Processing:**
- "High guidance" → articulate_first=true, show_confidence=true, show_reasoning=true, options_not_recommendations=true
- "Balanced" → articulate_first=true, show_confidence=true, show_reasoning=false, options_not_recommendations=true
- "Low guidance" → articulate_first=false, show_confidence=false, show_reasoning=false, options_not_recommendations=false
- "Custom mix" → proceed to individual steps

#### Step 20: Articulate First (if Custom)

```text
Header: "Articulate"
Question: "Require users to state their thinking before seeing analysis?"
Options:
- "Yes" - Prevents anchoring, keeps user in control
- "No" - Users can ask directly, AI responds immediately

Teaching: Core articulate-first pattern toggle
Collecting: articulate_first
```

#### Step 21: Show Confidence (if Custom)

```text
Header: "Confidence"
Question: "Show confidence levels in responses?"
Options:
- "Yes" - Helps users calibrate trust ([HIGH], [MEDIUM], [LOW])
- "No" - Cleaner output, user trusts their judgment

Teaching: Visible confidence aids decision-making
Collecting: show_confidence
```

#### Step 22: Show Reasoning (if Custom)

```text
Header: "Reasoning"
Question: "Show reasoning chains (how AI arrived at conclusions)?"
Options:
- "Yes" - Transparent, helps users verify logic
- "No" - Just conclusions, user requests details if needed

Teaching: Transparency vs brevity trade-off
Collecting: show_reasoning
```

#### Step 23: Options vs Recommendations (if Custom)

```text
Header: "Options"
Question: "Present options with trade-offs, or make recommendations?"
Options:
- "Options" - User decides from alternatives
- "Recommendations" - AI suggests best choice

Teaching: Options preserve user agency
Collecting: options_not_recommendations
```

---

### Section 7: Confirmation (Step 24)

#### Step 24: Confirm & Save (Gate 4: Destructive)

```text
Header: "Confirm"
Question: "Save this configuration?"

[Display Configuration Summary]
─────────────────────────────
Business: {business_name}
Domain: {domain}
Assistant: {assistant_name} ({style})

Knowledge domains: {domains joined}
Boundaries: {boundaries joined}

Escalation triggers: {triggers joined}

Reasoning protocol:
  - Articulate first: {articulate_first}
  - Show confidence: {show_confidence}
  - Options mode: {options_not_recommendations}
─────────────────────────────

Options:
- "Save and start" - Create config files
- "Go back" - Modify choices

Teaching: Files written to features/voice-gateway/configs/
Collecting: confirmed
```

---

## Output Files

After completion, wizard creates:

```text
~/.claude/features/voice-gateway/configs/
  {business-name}-config.yaml    # Voice gateway configuration
  {business-name}-reasoning.json # User's reasoning for each decision
```

### YAML Config Structure

See `business-config.yaml.template` for full schema. Key sections:

```yaml
business:        # Name, domain, created date
persona:         # Name, style, greeting, clarification
knowledge:       # Domains, boundaries, sources
operations:      # Escalation triggers, message, confidence thresholds
voice:           # STT/TTS settings
accessibility:   # Default input/output modes
reasoning:       # Articulate-first protocol settings
emergent_ui:     # Computed modality settings
```

### Reasoning Log Structure

```json
[
  {
    "step": "business_vision",
    "user_initial_view": "Team analysis",
    "final_decision": "technology",
    "timestamp": "2026-01-27T..."
  },
  {
    "step": "persona_vision",
    "user_initial_view": "Professional",
    "final_decision": "professional",
    "timestamp": "2026-01-27T..."
  }
]
```

---

## Integration with Voice Gateway

After onboarding:

```bash
# 1. Set API keys
export DEEPGRAM_API_KEY=...
export ELEVENLABS_API_KEY=...
export ANTHROPIC_API_KEY=...

# 2. Start gateway with config
python features/voice-gateway/voice-gateway.py --config configs/{business}-config.yaml

# 3. Connect from mobile app
```

---

## CLI fallback (removed 2026-05-21)

The legacy `scripts/onboard-business.py` CLI wizard was removed in PR
hitl-unify-2026-05-21 — caller-check found zero programmatic callers and
V>>'s explicit ratification authorised deletion. The TUI wizard at
`skills/onboarding-businesses/SKILL.md` (invoked as `/onboard-business`,
built on `/creating-wizards` per the DUAL-PARAMOUNT model) is now the only
entry path.

For non-interactive scripted onboarding, write the YAML config directly
against the canonical template at
`features/voice-gateway/business-config.yaml.template` — that file is the
single source of truth for the schema (business identity, persona,
knowledge domains/boundaries, operational escalation rules, voice
provider config). Substitute the `{{ jinja_placeholder }}` fields with
your business values and drop the result at
`features/voice-gateway/business-config.yaml`.

Do NOT inline-duplicate the schema here — keeping a second copy in this
SKILL.md would create a synchronisation hazard with the template file
(any future schema change would silently diverge). The template is
authoritative; this skill documents the wizard, not the schema.

The articulate-first interactive flow in the TUI wizard does not generalise
to headless use — the wizard's value is the teaching+collecting bidirectional
pattern, which assumes a human operator. For pure automation, the YAML
template at the canonical path is the contract.

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| SKILL.md load | ~1200 |
| Full wizard (24 steps) | ~3500-4500 |

Higher than Python CLI (0 tokens) but provides integrated experience with reasoning capture.

---

## Related Skills

- `reasoning-with-ai` - The pattern being configured
- `creating-wizards` - Wizard design patterns and anti-patterns
- `asking-users` - PARAMOUNT user consultation protocol

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-27 | TUI wizard rewrite with AskUserQuestion, articulate-first two-phase pattern |
| 1.0.0 | 2026-01-27 | Initial creation with Python CLI wizard |

---

*Build your Platform by using the Platform's capabilities.*

**
