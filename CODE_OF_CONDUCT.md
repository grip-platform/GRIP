# Code of Conduct

GRIP is an AI infrastructure project where humans and AI systems collaborate on
development, review, and self-improvement. This Code of Conduct reflects that
unique context. Where a conventional code of conduct relies on shared values
alone, GRIP backs its expectations with mechanical enforcement — safety gates,
automated review, and auditable decision logs.

## Our Commitment

We are committed to providing a welcoming, respectful, and harassment-free
environment for everyone, regardless of age, body size, visible or invisible
disability, ethnicity, sex characteristics, gender identity and expression,
level of experience, education, socio-economic status, nationality, personal
appearance, race, caste, colour, religion, or sexual identity and orientation.

## Expected Behaviour

- Demonstrate empathy and kindness towards other people
- Respect differing opinions, viewpoints, and experiences
- Give and gracefully accept constructive feedback
- Accept responsibility and apologise to those affected by mistakes
- Focus on what is best for the community, not just for individuals

## Unacceptable Behaviour

- Harassment, intimidation, or discrimination of any kind
- Sexualised language or imagery, and unwanted sexual attention or advances
- Trolling, insulting or derogatory comments, and personal or political attacks
- Publishing others' private information without explicit permission
- Conduct which could reasonably be considered inappropriate in a professional
  setting

## AI-Human Collaboration

GRIP is a system where AI participates in development through PR review, skill
generation, and recursive self-improvement (RSI) loops. This creates specific
obligations for all participants:

### Transparency

- AI-generated contributions must be clearly attributable to their human
  operator
- Human operators remain accountable for all AI-generated output that enters
  the project
- Automated decisions (gate blocks, review scores, merge recommendations) must
  be auditable

### RSI Safety

- RSI loops must operate within defined safety gates; bypassing gates is a
  conduct violation
- Autonomous loops must not exceed their declared scope without human approval
- If an RSI loop produces unexpected or potentially harmful output, halt and
  report immediately
- Max 3 fix iterations per PR before escalating to a human reviewer

### Session Data and Privacy

- Session data, KONO memory, and instance serialisation files are personal to
  each operator
- Do not access, share, or exfiltrate another operator's session data without
  explicit consent
- Genome and learning data belong to the operator who generated them

## Mesh Network Conduct

GRIP's mesh network enables knowledge sharing between peers. Participants must:

- **Obtain consent** before connecting to another peer's GRIP instance
- **Respect data boundaries** — shared knowledge is read-only unless explicitly
  granted write access
- **Never exfiltrate** proprietary skills, genome data, or session context from
  a peer's instance
- **Disconnect cleanly** when asked; persistent reconnection after disconnection
  is harassment

Examples of mesh network abuse:

- Connecting to a peer's instance without their knowledge or consent
- Extracting skills or genome data to use outside the agreed sharing scope
- Using mesh access to monitor another operator's activity

## Proprietary Information

GRIP is proprietary software owned by CodeTonight (Pty) Ltd. Contributors must:

- Never commit secrets, API keys, credentials, or `.env` files
- Respect git-crypt boundaries — encrypted files exist for a reason
- Not redistribute proprietary GRIP components outside the terms of the licence
- Report any suspected IP or trade secret exposure to the maintainers
  immediately

## Enforcement

### Reporting

Report violations privately to the maintainers via the project's GitHub
Security Advisories. All reports will be reviewed and investigated promptly,
fairly, and confidentially.

- **Initial response**: within 48 hours
- **Assessment**: within 7 days
- **Resolution**: within 30 days

### Consequences

Community leaders will follow these enforcement guidelines:

1. **Warning** — A written notice clarifying the nature of the violation, with
   an explanation of why the behaviour was inappropriate. A public apology may
   be requested.

2. **Temporary Restriction** — Limited participation in community spaces for a
   defined period. This includes restricted access to repositories, mesh
   network, and communication channels. Violating the restriction may lead to
   permanent removal.

3. **Permanent Removal** — Irrevocable exclusion from all community spaces,
   repositories, and mesh network access.

### Enforcement Responsibility

Community leaders are responsible for clarifying and enforcing these standards.
They will take appropriate and fair corrective action in response to any
behaviour that they deem inappropriate, threatening, offensive, or harmful.

## Scope

This Code of Conduct applies to:

- All GRIP repositories and communication channels
- GRIP mesh network interactions between peers
- Public representation of the GRIP community (conferences, social media,
  online forums)
- AI-generated actions performed on behalf of GRIP contributors

## Attribution

Adapted from the [Contributor Covenant](https://www.contributor-covenant.org),
version 2.1, with GRIP-specific extensions for AI-human collaboration, mesh
network conduct, and RSI safety.
