"""Guardrail layer for the CivicReach agent.

Deterministic keyword detectors (crisis, hostility, injection, scope, PII,
ambiguity) run first in the agent loop. The LLM intent classifier
(classifier.py) adds a second layer behind the keyword checks for defense
in depth. Hostility is non-blocking and lowest priority: crisis (and
injection, and scope) always win over it.
"""
