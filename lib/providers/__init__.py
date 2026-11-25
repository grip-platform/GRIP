"""GRIP provider clients for Multi-Provider MoE Architecture."""


def shield_request(payload, to=None):
    """``payload`` with its prose scrubbed by lib/review_redaction.py before a
    provider sees it, in every invocation shape. With no shield to load it
    raises OSError, so nothing is sent; a failed scrub raises ShieldFailed, also
    an OSError."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(payload, to=to)
