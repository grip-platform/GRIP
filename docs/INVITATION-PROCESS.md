# GRIP Invitation and Onboarding Process

**Internal document — not published on the public site.**

---

## Overview

GRIP is distributed by invitation only via private GitHub forks.
This document codifies the full process from initial enquiry to
evaluation completion or conversion to full licence.

---

## Process Flow

```
1. ENQUIRY     Visitor fills form on grip-preview.vercel.app
       |
2. QUALIFY     CodeTonight reviews: org size, use case, team maturity
       |
3. LEGAL       Send Evaluation Licence + CLA for signature
       |
4. PROVISION   Create private org fork on GitHub
       |
5. KEY ISSUE   Generate git-crypt key, share via secure channel
       |
6. ONBOARD     Run /activate-grip on their fork
       |
7. EVALUATE    90-day evaluation window with support
       |
8. CONVERT     Full licence agreement or graceful offboard
```

---

## Step 1: Enquiry

- Visitor fills the "Request Invitation" form at grip-preview.vercel.app
- Form submissions arrive via Formspree to development@codetonight.co.za
- Fields: Organisation, Contact Name, Email, Team Size, Use Case
- Auto-response confirms receipt (configured in Formspree dashboard)

## Step 2: Qualification

Review criteria:

| Factor | Minimum | Ideal |
|--------|---------|-------|
| Team size | 1+ developers | 3-15 developers |
| AI maturity | Using Claude Code | Active Claude Code users |
| Use case | Clear problem statement | Quantifiable inefficiency |
| Engagement | Responsive to emails | Attended Meet & Greet |

Decision: APPROVE, DEFER (needs Meet & Greet), or DECLINE (with reason).

## Step 3: Legal

Send two documents for signature:

1. **Evaluation Licence Agreement** (`site/EVALUATION-LICENCE.md`)
   - 90-day evaluation period
   - Internal evaluation only, no production use
   - All IP remains with CodeTonight
   - Confidentiality obligations

2. **Contributor Licence Agreement** (`site/CLA.md`)
   - Required if evaluator plans to contribute back
   - Copyright assignment to CodeTonight
   - Licence-back to contributor
   - Optional at evaluation stage; required for contribution

Signature method: Email confirmation or DocuSign (for enterprise).

## Step 4: Provisioning

Create a private fork for the evaluating organisation:

```bash
# Create org fork (GitHub CLI)
gh repo fork grip-platform/GRIP --org <evaluator-org> --fork-name GRIP

# Set fork to private
gh repo edit <evaluator-org>/GRIP --visibility private

# Add evaluator team as collaborators (read access)
gh api repos/<evaluator-org>/GRIP/collaborators/<username> -X PUT -f permission=pull
```

Ensure the fork:
- Is private (never public)
- Has branch protection on main
- Has upstream sync enabled for improvements

## Step 5: Key Issuance

Generate a per-org git-crypt key for the evaluator:

```bash
# Generate evaluator key (in upstream repo)
git-crypt add-gpg-user --key-name eval-<org> <evaluator-gpg-key>

# Or use symmetric key export
git-crypt export-key --key-name eval-<org> /tmp/eval-<org>.key
```

### git-crypt Key Tiers

| Tier | Key Name | Access |
|------|----------|--------|
| Core | (default) | Skills, agents, modes, learning data |
| Business | `codetonight` | Invoices, prep docs, business data |
| Restricted | (no key issued) | Patent drafts, strategy docs |
| Evaluator | `eval-<org>` | Core access only, per-org key |

Share the key via:
- Encrypted email (PGP/GPG)
- Secure file transfer (not plain email)
- In-person during onboarding call

**Never share:**
- The `codetonight` key
- Access to `docs/restricted/**`
- Write access to upstream

## Step 6: Onboarding

Schedule a 30-60 minute onboarding session:

1. Evaluator clones their fork
2. Run `git-crypt unlock /path/to/eval-<org>.key`
3. Run `/activate-grip` wizard
4. Walk through key features: skills, modes, safety gates
5. Set up Slack channel for support (optional)

Provide:
- Quick-start guide (link to documentation.html)
- Key commands reference
- Support contact: development@codetonight.co.za

## Step 7: Evaluation

During the 90-day evaluation window:

- Weekly check-in (optional, by evaluator preference)
- Upstream sync for improvements (automatic via GitHub)
- Bug reports via GitHub Issues on their fork
- Feature requests documented for review

Track evaluation metrics:
- Sessions per week
- Skills created
- Issues reported
- Team adoption (number of active users)

## Step 8: Conversion

At evaluation end (day 75 — 15 days before expiry):

### Path A: Convert to Full Licence
- Negotiate full licence terms
- Upgrade from evaluation to production licence
- Maintain fork with expanded access
- Ongoing support agreement

### Path B: Extend Evaluation
- Additional 90 days if evaluator needs more time
- Requires written agreement
- Maximum 2 extensions (270 days total)

### Path C: Graceful Offboard
- Evaluator deletes fork and all copies
- Evaluator confirms deletion in writing
- Revoke git-crypt key
- Remove collaborator access

---

## What Evaluators Get

- Private fork of grip-platform/GRIP
- git-crypt key scoped to evaluation tier
- Access to ~150 skills, 23 agents, 30 modes
- Upstream sync for improvements during evaluation
- Support channel (email or Slack)

## What Evaluators Do Not Get

- Access to `docs/restricted/**` (encrypted, no key)
- Access to `prep/**` (encrypted, different key)
- Write access to upstream repository
- Right to sub-licence or share
- Production use rights

---

## Contact

All enquiries: development@codetonight.co.za
Company: CodeTonight (Pty) Ltd

---

*Internal document. Do not distribute externally.*
