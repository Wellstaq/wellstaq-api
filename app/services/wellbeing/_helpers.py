"""
app/services/wellbeing/_helpers.py
"""

from __future__ import annotations

# Minimum number of distinct participants required before an aggregate
# wellbeing score is returned. Below this, individual scores could be
# effectively de-anonymized by process of elimination in a small
# branch/team — so the aggregate is suppressed entirely rather than
# shown with a misleading precision. This number is a product/privacy
# decision, not a technical one — 5 is a reasonable starting point but
# should be owned and revisited deliberately, not treated as settled.
ANONYMITY_THRESHOLD = 5