from src.agent import CivicReachAgent, FoodPathAgent, first_turn_disclosure
from src.web import app


def test_foodpath_is_the_public_agent_name_with_compatibility_alias():
    assert FoodPathAgent is CivicReachAgent


def test_disclosure_uses_brand_and_independence_boundary():
    disclosure = first_turn_disclosure("en")
    assert "FoodPath" in disclosure
    assert "independent" in disclosure.lower()
    assert "not a DSS caseworker" in disclosure


def test_api_metadata_uses_foodpath_name():
    assert app.title == "FoodPath — NC FNS Eligibility Guide"
