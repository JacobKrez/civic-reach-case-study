"""Single source of truth for the product name.

The product is intentionally NOT named after the company/agency being applied
to (the NC FNS / SNAP program). Going by ``FoodPath`` keeps the application
name neutral and avoids implying an official endorsement that does not exist.
This is deliberate and is captured in README.md so a reviewer reading the repo
does not read the choice as accidental.

All user-visible response functions (disclosure, greeting, injection,
scope redirect, not-a-person, expedited, benefit-amount, application-status
— EN and ES) import :data:`PRODUCT_NAME` from this module. A future rename is
a one-line change here, and ``tests/test_branding.py`` enforces that no
response function reintroduces a literal brand string.

The ``branding.py`` module MUST NOT import anything from the rest of the
package so it can be imported anywhere without creating circular dependencies.
"""

from __future__ import annotations

PRODUCT_NAME: str = "FoodPath"

__all__ = ["PRODUCT_NAME"]