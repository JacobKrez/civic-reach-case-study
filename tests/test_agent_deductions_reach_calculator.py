"""W26: volunteered deduction fields reach the calculator.

The deterministic NC FNS calculator (``src.calculator.calculate_eligibility``)
accepts four optional deduction inputs:

  - earned_monthly_income
  - dependent_care_monthly
  - medical_expenses_monthly
  - shelter_costs_monthly

When any of them is non-zero, the calculator runs the real federal net-income
deduction chain (7 CFR §273.9(d), FY2026 standards) and the agent's verdict
is the result of that chain — not a comparison of gross income against the
130% limit.

The state model, the update_state tool schema, the system prompt, the
handoff summary, and the knowledge base all support the four deduction
fields. The agent's ``_calculate_once`` method was, however, only passing
the three required intake fields (household_size, gross_monthly_income,
has_elderly_or_disabled_member) to ``calculate_eligibility``. The wired-
through chain existed in the calculator but not in the agent.

This test exercises the full agent stack with a scripted LLM that emits
update_state tool calls carrying the four deduction fields, and asserts
that the agent's reported ``eligibility_result`` matches the calculator's
direct output across a matrix of deduction inputs.

The flagship case comes straight from
``knowledge_base/03_deductions_and_household.md``::

    household = 1, gross = $1,800/mo, all earned, no elderly/disabled
    -> net income = 1,231.00, likely eligible under the applicable test.

Before the fix, the agent reported 1,800.00 / qualifies=False because the
deduction never reached the calculator.
"""

from __future__ import annotations

import pytest

from src.agent import CivicReachAgent
from src.calculator import calculate_eligibility
from src.guardrails.classifier import CLASSIFIER_SYSTEM_PROMPT


@pytest.fixture(autouse=True)
def disable_classifier(monkeypatch):
    """Keep scripted extraction tests independent of classifier routing."""
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")


class ScriptedLLM:
    """Scripted LLM that emits a fixed sequence of update_state tool calls.

    The classifier is bypassed via the GUARDRAIL_CLASSIFIER=off env var
    (see above). The agent's main ``chat`` call receives the responses
    in order; ``extra`` is forwarded unchanged so each turn can pass its
    own update_state payload.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if system == CLASSIFIER_SYSTEM_PROMPT:
            return {"content": "normal", "tool_calls": []}
        if self._responses:
            return self._responses.pop(0)
        return {"content": "ok", "tool_calls": []}


def _run_agent_with_deductions(
    household_size: int,
    gross_monthly_income: float,
    has_elderly_or_disabled: bool,
    earned_monthly_income: float = 0.0,
    dependent_care_monthly: float = 0.0,
    medical_expenses_monthly: float = 0.0,
    shelter_costs_monthly: float = 0.0,
) -> dict:
    """Drive the agent through 4 scripted turns that populate state, then
    return the agent's cached ``eligibility_result``.

    Turn 1: household_size
    The federal deduction chain is fully wired into the calculator by
    W26, so the deduction fields only need to be in state BEFORE the
    verdict is locked. The agent's verify/extract phase runs once per
    turn while state is still incomplete; once ``_eligibility_result``
    is set, the agent switches to an explanation-only phase that does
    not re-apply update_state tool calls. So we hand the model all
    three required intake fields plus every deduction the user
    volunteers by the time the third field is supplied — anything
    arriving on a later turn is explanation-only and never lands in
    state.

    Turn 1: household_size
    Turn 2: gross_monthly_income + earned_monthly_income + dependent_care_monthly
    Turn 3: has_elderly_or_disabled_member + medical_expenses_monthly + shelter_costs_monthly
    """
    responses = [
        # Turn 1: household_size
        {"content": "ok", "tool_calls": [{"name": "update_state", "input": {"household_size": household_size}}]},
        # Turn 2: gross + earned + dependent_care
        {"content": "ok", "tool_calls": [{"name": "update_state", "input": {
            "gross_monthly_income": gross_monthly_income,
            "earned_monthly_income": earned_monthly_income,
            "dependent_care_monthly": dependent_care_monthly,
        }}]},
        # Turn 3: medical + shelter + elderly/disabled — verdict locks here
        {"content": "ok", "tool_calls": [{"name": "update_state", "input": {
            "has_elderly_or_disabled_member": has_elderly_or_disabled,
            "medical_expenses_monthly": medical_expenses_monthly,
            "shelter_costs_monthly": shelter_costs_monthly,
        }}]},
    ]
    agent = CivicReachAgent(llm_client=ScriptedLLM(responses))
    for _ in range(3):
        agent.turn("placeholder")
    assert agent.eligibility_result is not None, "agent did not compute a result"
    return agent.eligibility_result


# ---------------------------------------------------------------------------
# Flagship case from knowledge_base/03_deductions_and_household.md
# ---------------------------------------------------------------------------
def test_flagship_case_deductions_flip_verdict():
    """The documented flagship example: household of 1, $1,800 gross all
    earned. Without W26 the agent reported 1800.0 / qualifies=False; with
    the fix, the calculator runs the deduction chain and reports
    1231.0 / qualifies=True."""
    agent_result = _run_agent_with_deductions(
        household_size=1,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
        earned_monthly_income=1800.0,
    )
    direct = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
        earned_monthly_income=1800.0,
    )
    assert agent_result["income_considered"] == direct["income_considered"] == 1231.0
    assert agent_result["qualifies"] is direct["qualifies"] is True
    assert agent_result["test_used"] == "gross"  # no elderly/disabled -> gross test
    assert agent_result["deductions"] is not None
    assert agent_result["deductions"]["earned_income"] == 360.0  # 20% of 1800


# ---------------------------------------------------------------------------
# Matrix: every deduction field, alone and combined, elderly and non-elderly.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case",
    [
        # (household_size, gross, has_elderly, earned, dependent_care, medical, shelter)
        # --- 1) no deductions: agent must match pre-W14 behavior ---
        {"id": "no_deductions_h1",     "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 0.0, "med": 0.0, "shelter": 0.0},
        # --- 2) earned only ---
        {"id": "earned_only_h1",       "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 1800.0, "dep": 0.0, "med": 0.0, "shelter": 0.0},
        # --- 3) dependent care only ---
        {"id": "dep_care_only_h1",     "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 200.0, "med": 0.0, "shelter": 0.0},
        # --- 4) medical only, household WITHOUT elderly/disabled: medical
        #        is ignored by the chain, so the result must match the
        #        "no deductions" case for this household ---
        {"id": "medical_ignored_h1",   "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 0.0, "med": 300.0, "shelter": 0.0},
        # --- 5) medical only, household WITH elderly/disabled: now the
        #        $35 threshold applies and the deduction runs ---
        {"id": "medical_elderly_h1",   "hh": 1, "gross": 1800.0, "elderly": True,
         "earned": 0.0, "dep": 0.0, "med": 300.0, "shelter": 0.0},
        # --- 6) shelter only, non-elderly: capped at $712 ---
        {"id": "shelter_nonelderly_h1", "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 0.0, "med": 0.0, "shelter": 900.0},
        # --- 7) shelter only, elderly: uncapped ---
        {"id": "shelter_elderly_h1",    "hh": 1, "gross": 1800.0, "elderly": True,
         "earned": 0.0, "dep": 0.0, "med": 0.0, "shelter": 2000.0},
        # --- 8) all four deductions combined, non-elderly ---
        {"id": "all_four_nonelderly_h1", "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 1800.0, "dep": 150.0, "med": 0.0, "shelter": 900.0},
        # --- 9) all four deductions combined, elderly ---
        {"id": "all_four_elderly_h1",    "hh": 1, "gross": 1800.0, "elderly": True,
         "earned": 1800.0, "dep": 150.0, "med": 200.0, "shelter": 900.0},
        # --- 10) household of 4, deductions push it over the line ---
        {"id": "hh4_earned_only",        "hh": 4, "gross": 3200.0, "elderly": False,
         "earned": 3200.0, "dep": 0.0, "med": 0.0, "shelter": 0.0},
    ],
    ids=[c["id"] for c in [
        {"id": "no_deductions_h1",     "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 0.0, "med": 0.0, "shelter": 0.0},
        {"id": "earned_only_h1",       "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 1800.0, "dep": 0.0, "med": 0.0, "shelter": 0.0},
        {"id": "dep_care_only_h1",     "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 200.0, "med": 0.0, "shelter": 0.0},
        {"id": "medical_ignored_h1",   "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 0.0, "med": 300.0, "shelter": 0.0},
        {"id": "medical_elderly_h1",   "hh": 1, "gross": 1800.0, "elderly": True,
         "earned": 0.0, "dep": 0.0, "med": 300.0, "shelter": 0.0},
        {"id": "shelter_nonelderly_h1", "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 0.0, "dep": 0.0, "med": 0.0, "shelter": 900.0},
        {"id": "shelter_elderly_h1",    "hh": 1, "gross": 1800.0, "elderly": True,
         "earned": 0.0, "dep": 0.0, "med": 0.0, "shelter": 2000.0},
        {"id": "all_four_nonelderly_h1", "hh": 1, "gross": 1800.0, "elderly": False,
         "earned": 1800.0, "dep": 150.0, "med": 0.0, "shelter": 900.0},
        {"id": "all_four_elderly_h1",    "hh": 1, "gross": 1800.0, "elderly": True,
         "earned": 1800.0, "dep": 150.0, "med": 200.0, "shelter": 900.0},
        {"id": "hh4_earned_only",        "hh": 4, "gross": 3200.0, "elderly": False,
         "earned": 3200.0, "dep": 0.0, "med": 0.0, "shelter": 0.0},
    ]],
)
def test_agent_matches_direct_calculator(case):
    """Agent's eligibility_result must match a direct call to
    ``calculate_eligibility`` with the same arguments across the matrix."""
    agent_result = _run_agent_with_deductions(
        household_size=case["hh"],
        gross_monthly_income=case["gross"],
        has_elderly_or_disabled=case["elderly"],
        earned_monthly_income=case["earned"],
        dependent_care_monthly=case["dep"],
        medical_expenses_monthly=case["med"],
        shelter_costs_monthly=case["shelter"],
    )
    direct = calculate_eligibility(
        household_size=case["hh"],
        gross_monthly_income=case["gross"],
        has_elderly_or_disabled=case["elderly"],
        earned_monthly_income=case["earned"],
        dependent_care_monthly=case["dep"],
        medical_expenses_monthly=case["med"],
        shelter_costs_monthly=case["shelter"],
    )
    # The fields the agent's verdict and the calculator's must agree on.
    for key in ("qualifies", "income_considered", "limit",
                "test_used", "expanded_limit", "outcome", "household_size"):
        assert agent_result[key] == direct[key], (
            f"{case['id']}: {key} mismatch: agent={agent_result[key]!r} "
            f"direct={direct[key]!r}"
        )
    # The deductions breakdown must agree whenever any deduction input
    # is non-zero, and must be None when none are.
    if any(v > 0 for v in (case["earned"], case["dep"], case["med"], case["shelter"])):
        assert agent_result["deductions"] == direct["deductions"], (
            f"{case['id']}: deductions breakdown mismatch: "
            f"agent={agent_result['deductions']!r} direct={direct['deductions']!r}"
        )
    else:
        assert agent_result["deductions"] is None
        assert direct["deductions"] is None


# ---------------------------------------------------------------------------
# Cache invalidation: a deduction disclosed AFTER a verdict must recompute.
# ---------------------------------------------------------------------------
def test_deduction_disclosed_after_verdict_invalidates_cache():
    """W26 second invariant: changing a deduction field after a verdict has
    been cached must invalidate the cache and the next ``_calculate_once``
    must recompute with the deduction applied.

    The agent's normal ``turn()`` flow short-circuits the extract phase
    once a verdict is locked, so we exercise the cache invalidation
    primitive directly: lock a verdict, then call ``_apply_tool_calls``
    with an update_state that flips ``earned_monthly_income`` from 0.0
    to 1800.0. The change must be detected as a correction (because the
    field is now in ``_INTAKE_FIELDS``), the cached result must be
    cleared, and the next ``_calculate_once`` must produce the
    deduction-aware verdict.
    """
    responses = [
        {"content": "ok", "tool_calls": [{"name": "update_state", "input": {"household_size": 1}}]},
        {"content": "ok", "tool_calls": [{"name": "update_state", "input": {"gross_monthly_income": 1800.0}}]},
        {"content": "ok", "tool_calls": [{"name": "update_state", "input": {"has_elderly_or_disabled_member": False}}]},
    ]
    agent = CivicReachAgent(llm_client=ScriptedLLM(responses))
    for _ in range(3):
        agent.turn("placeholder")
    # Sanity: the verdict was locked with no deductions: 1800.0 / True under
    # the current NC BBCE gross screen.
    assert agent.eligibility_result is not None
    assert agent.eligibility_result["income_considered"] == 1800.0
    assert agent.eligibility_result["qualifies"] is True

    # Now the user volunteers that all $1,800 is earned income. We
    # drive the same code path the agent's extract phase would use,
    # bypassed here because the extract phase is short-circuited once
    # the verdict locks.
    new_corrections = agent._apply_tool_calls(
        [{"name": "update_state", "input": {"earned_monthly_income": 1800.0}}]
    )
    # The field flip must be detected as a correction: the field
    # already had a non-None value (0.0) that changed to a different
    # non-None value (1800.0).
    assert any(c["field"] == "earned_monthly_income" for c in new_corrections), (
        "earned_monthly_income flip was not detected as a correction; "
        "cache invalidation will not fire"
    )
    # The cached result must have been cleared.
    assert agent._eligibility_result is None, (
        "cache invalidation did not fire: cached result still present "
        "after earned_monthly_income changed"
    )
    # The next calculate_once must produce the deduction-aware verdict.
    agent._calculate_once()
    agent_result = agent.eligibility_result
    assert agent_result is not None
    direct = calculate_eligibility(
        household_size=1, gross_monthly_income=1800.0,
        has_elderly_or_disabled=False, earned_monthly_income=1800.0,
    )
    assert agent_result["income_considered"] == direct["income_considered"] == 1231.0
    assert agent_result["qualifies"] is True
