"""Knowledge absorption pipeline.

Each absorber is a single-purpose function-first module. They share:

* a common :class:`AbsorbRecord` shape written to ``state/copilot/knowledge.jsonl``
* the work-relevance filter from :mod:`lib.aide.relevance`
* explicit ``TEST_FIXTURE`` markers — never silent mocks
"""

from .base import AbsorbRecord, write_record

__all__ = ["AbsorbRecord", "write_record", "gog", "slack", "whatsapp"]
