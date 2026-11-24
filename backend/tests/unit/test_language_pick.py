"""Language pick rule for short questions (found by the M2 parity e2e on real models)."""

from __future__ import annotations

import pytest

from codura_docs.adapters.langdetect.lingua_detector import pick

RULE = {"min_confidence": 0.5, "min_lead_confidence": 0.35, "min_lead": 0.1}


@pytest.mark.parametrize(
    ("ranked", "expected"),
    [
        ([("en", 0.82), ("fr", 0.08)], "en"),  # confident
        ([("en", 0.498), ("de", 0.36), ("es", 0.08)], "en"),  # "And how do I reset it?": clear lead
        ([("en", 0.45), ("de", 0.40)], None),  # too close to call → history decides
        ([("en", 0.30), ("de", 0.10)], None),  # too weak overall
        ([(None, 0.9), ("en", 0.05)], None),  # outside the launch set
        ([], None),
        ([("es", 0.4)], "es"),  # single candidate, above the lead floor
    ],
)
def test_pick(ranked: list[tuple[str | None, float]], expected: str | None) -> None:
    assert pick(ranked, **RULE).language == expected
