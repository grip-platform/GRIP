# Name Things So They Say What They Are: annex

The narrative behind `rules/clear-naming.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## Origin

On 2026-11-19 V>> asked GRIP to remember that he prefers clear naming, because items named by bare codes in a private store were hard to tell apart. He added: "There are only 2 hard things in Computer Science. 1. Naming things 2. Cache Invalidation 3. Off-by-one errors."

## Why the code stays on shared surfaces

`rules/personal-matters-stay-private.md` keeps names, identifiers and descriptions of personal matters out of GRIP, so GRIP refers to a matter by its code. The code is also the stable key: a matter frame, a command argument, a memory pointer or a plan names `m99` alone and still finds the folder after its slug changes. Only the folder name carries the slug, which serves the reader of the private store, where the operator keeps and finds his files.

## Why folder names need care

git-crypt encrypts file contents, not paths. A folder name in a private repository is readable to the hosting platform and to anyone who can list the repository. So a slug describes the matter, and never carries a private person's name or a child's details. When the subject itself is sensitive, the operator chooses between a plain slug, a discreet one, or the bare code with an encrypted index, because only the operator can weigh clarity against what the host can read.

## Falsification

This rule is wrong if, over the next 20 items named under it, a clear name carries a private matter onto a shared surface; then tighten the code-only clause. It is also wrong if the operator still has to ask which item is which; then the slugs are not plain enough. Track the renames the operator asks for after the fact (target 0).
