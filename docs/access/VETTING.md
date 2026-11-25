# Access vetting — grip-unlock v1

How access requests to GRIP/HAPPIverse are vetted and decided. Council-ratified shape
(seal `5d55b65c58c808aa`, `plans/ULTRAPLAN-grip-unlock-login.md`); operational surface
is the `access-request` issue template plus the `grip-unlock` approval subcommands
(`request-list` / `notify` / `approve` / `deny` / `sweep`).

## Ground rules

- **Public footprint only.** Vetting uses information the requester published or linked
  themselves: GitHub history, stated affiliation, the links they supplied. No invasive
  research, no background-checking services, no contacting third parties about the
  requester. Proportionate and lawful for an OSS community.
- **~15 minutes per request, by construction.** If the checklist cannot be completed
  from public information in about 15 minutes, the request is not ready — ask the
  requester for better links rather than digging deeper.
- **Every deny is explainable.** The criteria below are the written reasons; a deny
  cites which criterion was not met, courteously.
- **Every decision is recorded.** Each grant or deny emits a signed IDR decision record
  (requester, evidence summary, approvers, verdict) — the same tamper-evident record
  GRIP keeps for its own reasoning.

## The checklist (complete all five from public info)

1. **Identity coherence** — the GitHub handle exists, is plausibly the named person,
   and the supplied links point at the same identity. Red flag: a days-old account
   with no history requesting private-overlay access.
2. **Track record** — real public activity (commits, projects, posts, talks) consistent
   with the stated intent. Quality over volume; a small genuine project beats a wall of
   forks.
3. **Stated intent is concrete** — the "why" names something they actually want to do
   with the access, at the scope they asked for. Vague enthusiasm is a community-role
   signal, not a private-overlay one.
4. **Affiliation sanity** — the stated affiliation checks out against their public
   footprint, or is honestly "independent". Undisclosed commercial affiliation with a
   conflicting interest is a deny criterion.
5. **Scope proportionality** — the access sought is the smallest that serves the intent.
   Someone who wants to file issues and discuss does not need org write. Approvers may
   grant a smaller scope than requested (say so in the decision comment).

## Approval rules

| Scope | Meaning | Approvals required |
|---|---|---|
| `community-role` | public community membership / role | **1** roster approver |
| `private-overlay` | org/repo write, private substrate, infra | **2 distinct** roster approvers |

- The approver roster is `config/team-ratification.json` `ratifiers` — one roster, one
  truth (the same humans whose ratification counts for PR merges).
- Approvals are attributed: each approver runs `grip-unlock approve <issue> --as <name>`
  themselves; the attribution comment on the issue is the public record. The same person
  approving twice still counts once — dual approval means two distinct humans.
- A deny by any roster approver, at any point, ends the request (deny wins over a
  pending first approval).

## Timers and rails

- **72h no-response auto-deny.** A request with zero approver responses after 72 hours
  is automatically declined with a courteous reply inviting re-application
  (`grip-unlock sweep`). A request with one approval standing is NOT auto-denied — it is
  waiting on the second approver, which is the bus-factor rail's territory.
- **Bus-factor rail.** If one approver is unreachable for 7 days or more, a single
  approver may decide a private-overlay request alone, PLUS a 7-day cooling-off period
  before provisioning is executed. The deciding approver states the rail is in use in
  the decision comment, so the record shows why one signature stands where two are
  required.

## What this gate cannot do

This gate governs **future** entrants only. It provides **no protection against anyone
who already holds a clone or an enrolled key** — decrypting an existing clone needs no
login, and no approval workflow can revoke bytes already on someone else's disk. The
control for departed collaborators is **key rotation** (plus source-rotation of any real
credentials in the old set), not this gate. And once GRIP is AGPL-public, the code is
public **by licence** — cloning it is a right, not a breach; this gate governs only the
private overlays (org/repo write, private substrate, secrets, infra) and community
membership. Any description of this gate that claims more than that is selling false
security.

## After approval

An approved decision **emits** the exact provisioning commands (org invitation /
collaborator add at the approved scope, and — for private-overlay — the
`grip-unlock issue` step that mints the suite admin-unlock code when the requester's
challenge arrives). The operator runs them; the gate never executes provisioning
itself in v1. Revocation is the same steps inverted, plus an IDR record of the
revocation decision.

---

Provenance: V>> (Laurie Scheepers) with Claude Fable 5 (`claude-fable-5`).
