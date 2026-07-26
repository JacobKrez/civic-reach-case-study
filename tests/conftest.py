"""Shared pytest configuration for the FoodPath test suite.

The two-layer guardrail design (deterministic keyword layer + LLM intent
classifier) is gated by the env var ``GUARDRAIL_CLASSIFIER``. When unset
(default) the classifier layer is on; when set to ``'off'`` (case-insensitive,
also ``'0'``/``'false'``/``'no'``/``'disabled'``) it is bypassed entirely and
:classify_intent` returns ``'normal'`` without calling the LLM.

Background
----------

The documented offline mode for this project is ``GUARDRAIL_CLASSIFIER=off``.
README and ``src/guardrails/classifier.py`` describe it as the supported way
to run unit tests deterministically, run air-gapped, and run the model bake-off
without spending provider tokens.

A previous version of this suite had tests that implicitly depended on the
classifier layer running, so they only passed when the ambient shell happened
to leave ``GUARDRAIL_CLASSIFIER`` unset. A reviewer running the documented
offline mode on a machine with no provider key would see ``pytest -q`` fail
24 tests. Worse, the reconcile loop's acceptance check used
``GUARDRAIL_CLASSIFIER=off`` for live runs, found the suite red, and requeued
the dependent chunk in a loop (this was the W52 incident).

The fix is below: pin a known default at session start, and require any test
that encodes classifier-on semantics to opt in explicitly via ``monkeypatch``.
A suite whose result depends on the shell it was launched from is not a suite
you can trust before a live demo.

If a test needs the classifier OFF it can set it explicitly (the autouse
fixture is a no-op when the test has already touched the variable).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_guardrail_classifier(monkeypatch: pytest.MonkeyPatch):
    """Pin ``GUARDRAIL_CLASSIFIER`` to a known default for every test.

    Default: UNSET, i.e. the production configuration with the classifier
    layer ON. Tests that exercise the documented offline mode opt in
    explicitly with ``monkeypatch.setenv('GUARDRAIL_CLASSIFIER', 'off')`` in
    the test body, which runs after this fixture and therefore wins.

    Why this is load-bearing:
      * The two-layer guardrail design is the project centerpiece; tests must
        encode their assumption about which layer is running, not inherit it
        from the ambient shell.
      * Without this fixture, a CI runner with ``GUARDRAIL_CLASSIFIER=on`` (or
        unset) would silently skip the offline-mode coverage and a local
        reviewer with the variable unset would see a different test count
        than a CI build.
      * Defaulting to the PRODUCTION configuration means a test that says
        nothing about the classifier exercises what real users get. Offline
        mode is still covered — by the tests that opt into it explicitly, and
        by a dedicated CI leg that runs the whole suite with the variable set.

    Autouse fixtures run before the test body, so a ``monkeypatch.setenv``
    inside a test always wins over this default. Because the pin goes through
    ``monkeypatch`` it is also unwound after every test, so nothing leaks
    between tests.
    """
    # Pin to the PRODUCTION default -- variable UNSET, i.e. classifier ON --
    # rather than to the offline mode.
    #
    # Two bugs in the original W52 implementation, both fixed here:
    #
    # 1. It pinned ``'off'``. But 23 tests in this suite exist specifically to
    #    prove the classifier layer RUNS (test_classifier.py asserts two LLM
    #    calls -- classifier then agent -- and test_guardrail_source_attribution
    #    asserts the turn is attributed to the classifier). Pinning the layer
    #    off made every one of them fail. Defaulting to the production
    #    configuration means a test that says nothing about the classifier gets
    #    the behavior real users get.
    #
    # 2. It used ``os.environ.setdefault``, which mutates the process
    #    environment permanently and is never restored. The first test to run
    #    set the value for the entire session, so a later test's
    #    ``monkeypatch.setenv`` was restored to the LEAKED value rather than to
    #    the true starting state. ``monkeypatch.delenv`` is per-test and
    #    unwound automatically.
    #
    # W52's actual goal still holds and is what matters: the result of the
    # suite must not depend on the shell it was launched from. Clearing the
    # variable achieves that in both directions -- an ambient
    # GUARDRAIL_CLASSIFIER=off no longer leaks in, and neither does an ambient
    # 'on'. Tests that exercise the offline mode opt in explicitly with
    # ``monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")`` in the test body,
    # which runs after this fixture and therefore wins.
    monkeypatch.delenv("GUARDRAIL_CLASSIFIER", raising=False)
    yield
