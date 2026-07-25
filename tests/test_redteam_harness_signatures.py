"""Regression tests for the red-team harness heuristic signatures (W47).

Defect 1 (W47)
--------------
The harness's pass criteria for the ``injection`` and ``off_topic``
categories are heuristic: the harness recognises the deterministic refusal
/ scope-redirect text by substring matching a signature. Before W47 those
signatures were hardcoded brand strings (``CivicReach``, ``North
Carolina``, ``eligibility``) imported from a literal in run_redteam.py.
The project was renamed CivicReach -> FoodPath, the refusal text changed
to begin ``I'm FoodPath, an independent assistant for an initial North
Carolina Food and Nutrition Services (FNS / SNAP) eligibility screen``,
and the hardcoded brand string match went blind. The injection category
scored 0% while detection was actually fine.

The fix is to derive the signature from the live response functions at
runtime. These tests assert the harness signature follows the response
text so a future rename / wording tweak can never silently blind the
harness again.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "redteam" / "run_redteam.py"

# Make src/ importable when loading the harness module by path.
sys.path.insert(0, str(REPO))


def _load_harness():
    """Load run_redteam.py as a standalone module.

    The harness lives in ``redteam/`` and is not normally on the Python
    path. Loading it by file path avoids polluting the package import
    surface while still giving us access to its helpers.
    """
    spec = importlib.util.spec_from_file_location("rt", str(HARNESS))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load_harness()


def test_refusal_signature_tracks_injection_response_english(harness):
    """The English refusal signature must match the live refusal text.

    This is the core W47 invariant: if ``injection_response('en')``
    changes in a way that still mentions an independent assistant for an
    initial North Carolina eligibility screen, the signature derived at
    runtime must track it. If a future contributor edits the response
    text and forgets to update the signature, this test fails loudly
    instead of letting the injection category silently score 0%.
    """
    from src.guardrails.injection import injection_response

    text = injection_response("en")
    assert harness._looks_like_refusal(text), (
        "harness._looks_like_refusal no longer matches "
        f"src.guardrails.injection.injection_response('en'); the response "
        "text was edited but the runtime-derived signature was not. Update "
        "run_redteam._refusal_signature and re-run."
    )


def test_scope_signature_tracks_scope_redirect_english(harness):
    """The English scope-redirect signature must match the live text."""
    from src.guardrails.scope import scope_redirect_message

    text = scope_redirect_message("en")
    assert harness._looks_like_scope_redirect(text), (
        "harness._looks_like_scope_redirect no longer matches "
        f"src.guardrails.scope.scope_redirect_message('en'); the redirect "
        "text was edited but the runtime-derived signature was not. Update "
        "run_redteam._scope_redirect_signature and re-run."
    )


def test_refusal_signature_tracks_injection_response_spanish(harness):
    """The Spanish refusal signature must also match (W19 multi-lingual)."""
    from src.guardrails.injection import injection_response

    text = injection_response("es")
    assert harness._looks_like_refusal(text), (
        "harness._looks_like_refusal no longer matches "
        f"src.guardrails.injection.injection_response('es'); the Spanish "
        "refusal signature drifted from the live response."
    )


def test_scope_signature_tracks_scope_redirect_spanish(harness):
    """The Spanish scope-redirect signature must match the live text."""
    from src.guardrails.scope import scope_redirect_message

    text = scope_redirect_message("es")
    assert harness._looks_like_scope_redirect(text), (
        "harness._looks_like_scope_redirect no longer matches "
        f"src.guardrails.scope.scope_redirect_message('es'); the Spanish "
        "redirect signature drifted from the live response."
    )


def test_refusal_signature_does_not_match_arbitrary_text(harness):
    """The signature must not be so loose it matches normal intake replies.

    W47 lesson: a 'fix' that relaxes the threshold to make the tests pass
    would hide real regressions forever. The signature must keep enough
    discrimination to reject a normal intake reply.
    """
    benign = (
        "Got it — 4 people in the household. How much is the household's "
        "gross monthly income?"
    )
    assert not harness._looks_like_refusal(benign), (
        "harness._looks_like_refusal matched a normal intake reply; the "
        "signature is too loose and will produce false-positive "
        "injection detections across the whole corpus."
    )


def test_summary_counts_keyword_and_classifier_attribution(harness):
    """W48 reports both guardrail layers without inferring from reply text."""
    results = [
        {
            "category": "crisis",
            "passed": True,
            "caught_by": ["keyword"],
            "turn_attribution": [{"guardrail_source": "keyword"}],
        },
        {
            "category": "crisis",
            "passed": True,
            "caught_by": ["classifier"],
            "turn_attribution": [{"guardrail_source": "classifier"}],
        },
    ]

    attribution = harness.summarize(results)["guardrail_layer_attribution"]

    assert attribution["case_counts"] == {"keyword": 1, "classifier": 1}
    assert attribution["event_counts"] == {"keyword": 1, "classifier": 1}
    assert attribution["by_category"]["crisis"] == {
        "keyword": 1,
        "classifier": 1,
    }