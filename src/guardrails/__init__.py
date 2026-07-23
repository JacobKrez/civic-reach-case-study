"""Guardrail layer for the CivicReach agent.

Deterministic keyword detectors (crisis, injection, scope, PII, ambiguity)
run first in the agent loop. The LLM intent classifier (classifier.py)
adds a second layer behind the keyword checks for defense in depth.
"""
