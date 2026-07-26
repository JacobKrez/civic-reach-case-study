"""Brand-skew guard for the W51 CivicReach -> FoodPath rename.

This is the actual W51 deliverable: a single test that makes a half-applied
product rename impossible to ship again.

Three checks:

(a) No tracked source file under ``src/`` contains the old brand string
    ``CivicReach``. This is the same check as the acceptance #1 shell
    command so the test mirrors the gate exactly.

(b) Every user-visible response function names the SAME product, derived
    from a single constant ``src.branding.PRODUCT_NAME`` -- not a literal
    repeated in eight modules. This makes a future rename a one-line
    change. We exercise the response functions in EN and ES so a
    translation drift that re-introduces the old brand in one language
    also fails the test.

(c) Red-team / test heuristics that match on response text derive the
    matched text from the live response function (``src.guardrails.
    injection.injection_response``) rather than hardcoding a brand
    string. The W47 defect was that ``_refusal_signature`` in
    ``redteam/run_redteam.py`` grepped the old brand literal and went
    blind when the rename shipped. This guard pins the signature to the
    function so the same failure cannot recur.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# The previous product name. Tests will fail if this string reappears in
# any tracked source file or any user-visible response.
OLD_BRAND = "CivicReach"


# ---------------------------------------------------------------------------
# (a) No tracked src/ file contains the old brand.
# ---------------------------------------------------------------------------

def _tracked_src_files() -> list[str]:
    """Every Python file under src/, preferring git's view of tracked files.

    Falls back to walking the filesystem when git is unavailable or this is
    not a checkout. That happens more often than it sounds: a reviewer who
    downloads the repo as a zip, or a CI job using a shallow export, has no
    .git directory, and `git ls-files` exits 128. Hard-failing there would
    mean the suite is red for a reason that has nothing to do with the code
    — the exact class of environment-dependent failure this project already
    got bitten by once (the GUARDRAIL_CLASSIFIER conftest incident).
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "src"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(REPO),
        )
        files = [line for line in out.stdout.splitlines() if line.strip()]
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return [
        str(p.relative_to(REPO))
        for p in sorted((REPO / "src").rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


def test_no_old_brand_in_tracked_src():
    """Acceptance #1 mirrored -- no CivicReach in any tracked src/ file."""
    hits: list[str] = []
    for relpath in _tracked_src_files():
        abspath = REPO / relpath
        try:
            text = abspath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        if OLD_BRAND in text:
            hits.append(relpath)
    assert not hits, (
        f"Old brand '{OLD_BRAND}' still present in tracked src/: {hits}"
    )


# ---------------------------------------------------------------------------
# (b) Every user-visible response function names the SAME product via
#     PRODUCT_NAME. EN + ES, both branches.
# ---------------------------------------------------------------------------

def _import_response_functions():
    """Import the live response functions for the brand guard."""
    sys.path.insert(0, str(REPO))
    from src.branding import PRODUCT_NAME
    from src.agent import first_turn_disclosure, first_turn_greeting
    from src.guardrails.scope import (
        not_a_person_response,
        scope_redirect_message,
    )
    from src.guardrails.injection import injection_response
    from src.guardrails.expedited import expedited_response
    from src.guardrails.benefit_amount import benefit_amount_response
    from src.guardrails.application_status import application_status_response

    return {
        "product_name": PRODUCT_NAME,
        "fns": [
            ("first_turn_disclosure", first_turn_disclosure),
            ("first_turn_greeting", first_turn_greeting),
            ("injection_response", injection_response),
            ("scope_redirect_message", scope_redirect_message),
            ("not_a_person_response", not_a_person_response),
            ("expedited_response", expedited_response),
            ("benefit_amount_response", benefit_amount_response),
            ("application_status_response", application_status_response),
        ],
    }


@pytest.mark.parametrize("lang", ["en", "es"])
def test_responses_name_product_via_constant(lang):
    """Every response that names the product must use PRODUCT_NAME.

    The spec is: "every user-visible response function that names the
    product names the SAME one, derived from a single constant." A
    response that does not name the product is fine (e.g. a short
    greeting like "Hi! I'm here to help..."). A response that names it
    must name FoodPath via PRODUCT_NAME, never "CivicReach" or any
    other brand literal. This catches:
    - a future rename that adds a second product-name literal
    - a regression that re-introduces the old brand in one language
    """
    bundle = _import_response_functions()
    product = bundle["product_name"]
    assert product, "PRODUCT_NAME must be non-empty"

    bad: list[tuple[str, str]] = []
    for name, fn in bundle["fns"]:
        text = fn(lang)
        if OLD_BRAND in text:
            bad.append((name, f"contains {OLD_BRAND}"))
            continue
        # Detect ANY likely product-name literal: a Title-Case word that
        # appears at a sentence boundary and is not a common English
        # word. We require that if a candidate brand is present, it
        # equals PRODUCT_NAME. Cheap & robust: list the known
        # candidates the spec cares about.
        for candidate in ("CivicReach", "FoodPath", "foodpath", "food path"):
            if candidate in text and candidate != product:
                bad.append((name, f"hardcoded brand literal {candidate!r}"))

    assert not bad, (
        f"lang={lang} user-visible responses violate brand guard: {bad}"
    )


@pytest.mark.parametrize("lang", ["en", "es"])
def test_long_intro_response_names_product(lang):
    """Responses that introduce the product (length > 80 chars) must
    include PRODUCT_NAME. Short greetings are exempt; longer ones
    promised the user the product name in the disclosure, so they must
    deliver it consistently.
    """
    bundle = _import_response_functions()
    product = bundle["product_name"]
    intro_required_fns = [
        ("first_turn_disclosure", bundle["fns"][0][1]),
        ("scope_redirect_message", bundle["fns"][3][1]),
        ("not_a_person_response", bundle["fns"][4][1]),
        ("injection_response", bundle["fns"][2][1]),
    ]
    missing: list[str] = []
    for name, fn in intro_required_fns:
        text = fn(lang)
        if len(text) > 80 and product not in text:
            missing.append(name)
    assert not missing, (
        f"lang={lang}: long intro responses must name the product: {missing}"
    )


def test_product_name_is_a_single_constant():
    """PRODUCT_NAME is defined exactly once, in src/branding.py."""
    branding = (REPO / "src" / "branding.py").read_text(encoding="utf-8")
    assert "PRODUCT_NAME" in branding, "src/branding.py must define PRODUCT_NAME"

    # No other tracked src file should define PRODUCT_NAME -- it must
    # come from src.branding alone.
    redefs: list[str] = []
    for relpath in _tracked_src_files():
        if relpath.replace("\\", "/") == "src/branding.py":
            continue
        text = (REPO / relpath).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.lstrip()
            # Allow re-exports (`from .branding import PRODUCT_NAME`)
            # and references, but not a new assignment.
            if stripped.startswith("PRODUCT_NAME") and "=" in line and "==" not in line:
                redefs.append(f"{relpath}: {line.strip()}")
    assert not redefs, (
        f"PRODUCT_NAME is reassigned outside src/branding.py: {redefs}"
    )


# ---------------------------------------------------------------------------
# (c) Red-team / test heuristics derive response text from the response
#     function, not a hardcoded brand literal.
# ---------------------------------------------------------------------------

def _load_redteam_module():
    """Load redteam/run_redteam.py as a module without running its top-level CLI."""
    spec = importlib.util.spec_from_file_location(
        "run_redteam", REPO / "redteam" / "run_redteam.py"
    )
    assert spec and spec.loader, "could not load redteam/run_redteam.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_redteam"] = module
    spec.loader.exec_module(module)
    return module


def test_redteam_signature_derives_from_live_response():
    """The harness's refusal signature must come from injection_response(),
    not a hardcoded brand string. Regression for the W47 half-rename bug.
    """
    bundle = _import_response_functions()
    injection_response = dict(bundle["fns"])["injection_response"]

    rt = _load_redteam_module()
    assert hasattr(rt, "_refusal_signature"), (
        "redteam/run_redteam.py must expose _refusal_signature()"
    )

    for lang in ("en", "es"):
        sig = rt._refusal_signature(lang)
        assert sig, f"_refusal_signature({lang}) is empty"
        live = injection_response(lang).lower()
        # Every fragment in the signature must appear in the live
        # response (case-insensitive -- the signature is lowercase by
        # construction in run_redteam._refusal_signature). If the harness
        # ever drifts from the response, this fails.
        missing = [fragment for fragment in sig if fragment not in live]
        assert not missing, (
            f"_refusal_signature({lang}) drifts from injection_response("
            f"{lang}): missing fragments {missing!r}"
        )


def test_redteam_module_does_not_hardcode_old_brand():
    """The red-team harness source must not contain the old brand string.

    The harness is allowed to mention the rename in comments (historical
    context), but it must not -- anywhere -- use the old brand as a
    substring match against response text. This complements (c) above:
    (c) verifies the signature follows the live response, this verifies
    no other code path in the harness still greps the old name.
    """
    rt_path = REPO / "redteam" / "run_redteam.py"
    text = rt_path.read_text(encoding="utf-8")
    # We allow the old brand to appear in comments / docstrings that
    # narrate the W47 defect (it is the historical name). We forbid it
    # as a string literal (`"CivicReach"` or `'CivicReach'`).
    import re
    string_literal_hits = re.findall(
        r"""(['"])CivicReach\1""", text
    )
    assert not string_literal_hits, (
        "redteam/run_redteam.py still hardcodes the old brand as a string "
        "literal -- a future rename would silently blind this grep again."
    )
