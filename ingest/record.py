"""Live dual-venue recorder: continuous top-of-book snapshots of BOTH
venues, source='live'. You will need live truth later to arbitrate what
historical bars cannot show.

Restart safety is a MEASUREMENT-INTEGRITY issue: persist stream cursors
(db.store cursors table) and commit them atomically with the rows they
cover, so a restart cannot re-stream old data and mint duplicate rows.
Record TRUE timestamps/delays, never intended ones.
"""
from __future__ import annotations


def run_recorder(*args, **kwargs):
    raise NotImplementedError(
        "loop: poll both venues -> upsert quotes(source='live') -> commit cursor atomically"
    )
