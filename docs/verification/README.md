# Verifying a GRIP decision record

GRIP signs its decision records. This directory publishes the keys you need to
check those signatures yourself, without asking us to vouch for anything.

## The keys

`grip-verify-keys.json` maps a key fingerprint to that key's raw public bytes,
in hex:

| Fingerprint | Algorithm | Size |
|---|---|---|
| `c5b55c42` | Ed25519 | 32 bytes |
| `ad0de15d` | ML-DSA-65 (post-quantum) | 1952 bytes |

Every record is signed twice, once with each. A fingerprint is the first eight
hex characters of the SHA-256 of the raw public key bytes, so you can confirm
that a key in this file is the key a record names, by hashing it yourself.

These are **public** keys. Publishing them is what they are for. The private
halves have never left the machine that signs.

## Worked example — check a real record in about a minute

`docs/measurements/seals/09defe4df1413685.idr.jsonl` is a signed record of a
review council that sat on 22 September 2026. To check it:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install cryptography   # the only dependency
python3 docs/verification/verify.py \
        docs/measurements/seals/09defe4df1413685.idr.jsonl \
        docs/verification/grip-verify-keys.json
```

Expected output:

```
  ed25519   VALID   key c5b55c42 (32B)
  mldsa65   VALID   key ad0de15d (1952B)

  message signed : sha256:6ae3482f04bc66b0b30f5dfd6ef0ad656a231b2e14bb4f40f52fb69ddbe3b087
  record id      : precog-1790058041-e81619d7

  VERIFIED — both signatures check against the published keys
```

`verify.py` imports no GRIP code. It reads the record, recomputes the hash, and
calls the standard `cryptography` library. You are welcome to read all 41 lines
of it, or to write your own — the recipe is below.

## The recipe, if you would rather write your own

The signed message is the SHA-256 of the record with its `audit` block removed,
serialised as JSON with sorted keys and no spaces, then prefixed `sha256:` and
signed as ASCII text:

```python
EXCLUDED = {"entry_hash", "chain_hash", "sequence",
            "signature", "key_fingerprint", "scheme"}

body = {k: v for k, v in sorted(record.items())
        if k != "audit" and k not in EXCLUDED}
content = json.dumps(body, sort_keys=True, separators=(",", ":"))
message = ("sha256:" + hashlib.sha256(content.encode()).hexdigest()).encode()
```

`EXCLUDED` matters even though the example record happens to contain none of
those six keys. They are written onto a record *after* it is hashed, so the
signer never hashed them and a checker must not either. Leave them in and you
will get the right answer for this record and the wrong one for a record that
carries any of them — which reads as a forged record rather than as a bug in
your checker.

Then verify `record["audit"]["ed25519"]["signature"]` and
`record["audit"]["mldsa65"]["signature"]` over `message`, using the key whose
fingerprint each limb names.

## Convince yourself it can fail

A checker that always says VALID tells you nothing. Change any character in the
record — a timestamp, a word of the verdict — and run it again. Both signatures
turn INVALID and the script exits non-zero. Change a byte of a published key
instead, and it reports that the key no longer binds to its fingerprint.

## When this key was published, provably

`grip-verify-keys.json.ots` is an OpenTimestamps proof. It commits the SHA-256 of
the key file into the Bitcoin blockchain, which fixes the date the key was
published in a way nobody — including us — can backdate or quietly revise.

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install opentimestamps-client
ots verify docs/verification/grip-verify-keys.json.ots
```

That matters because of the limitation in the section above. It does not prove
the key is ours. It does prove that *this* key is the one that was published on
that date, so a key swapped in later cannot be passed off as the original. The
file's hash at publication was:

    23e876cf533a0ef96074e36e3e394e3d36be80fe65481accbd83b22940a5064b

A freshly submitted proof takes a few hours to be included in a block. Until
then `ots verify` reports a pending calendar commitment rather than a block
height, which is expected rather than a failure.

## What this does not prove, stated plainly

Checking a signature proves the record has not been altered since it was signed
by whoever holds the key named `c5b55c42`.

It does **not**, on its own, prove that key is ours. Someone could generate
their own key pair, sign a fabricated record with it, publish their own
fingerprint alongside it, and every internal check would pass. The fingerprint
binding catches a careless swap; it cannot catch a forger who is consistent.

The only thing that closes that gap is **where you get the key from**. A key you
fetch from the same place as the record you are checking is worth less than one
you fetch from somewhere the record's author does not control. That is why the
address matters more than the file, and why this file is a starting point rather
than the finish.

Concretely, here is a reader who does everything right and still ends up wrong.
They find a mirror of this repository, download the record and the key file from
it, run the checker, and get `VERIFIED`. Every step was correct and the answer
is worthless, because whoever set up that mirror supplied both halves: they
signed a record of their choosing with a key of their own and published the
matching fingerprint next to it. Nothing in the file, the checker or the
timestamp can detect this. Only fetching the key from somewhere that party does
not control can.

And that regress does not bottom out in anything this document can hand you. A
second address helps only to the extent you have reason to believe *that*
address is ours, which is a judgement about domains, hosting and who controls
them — not a cryptographic fact. Anyone telling you a published key removes the
need for that judgement is selling more certainty than cryptography provides.
What signatures genuinely buy you is narrower and still worth having: nobody can
alter a record after the fact without the alteration showing.
