"""0.5.0 Step 3 (AST) — the asset/version/snapshot model.

A new, cohesive package (plan operating rule 2's named exception — "a new
*cohesive* package where the requirement genuinely introduces a new
concern"), sitting BESIDE ``ai/v2/service.py`` rather than inside or
shadowing it.

- ``identity.py`` (S3a) — the human-quotable ID scheme, alias validation,
  display-name composition, the factory-reset epoch boundary.
- ``reads.py`` (S3a) — read-only query helpers (``list_assets``,
  ``ordered_snapshots``, ``snapshot_read_model``).
- ``service.py`` (S3b) — the write path: ``create_asset``, ``add_snapshot``,
  ``supersede_version_set``, ``restore_version_set``, ``rename_alias``.
  ``ai/v2/service.py``'s ``create_item``/``reupload_item``/``finalize_item``
  delegate into this module; ``dq_diagnostics/delivery.py`` remains the sole
  writer of ``dataset_family_id``/``delivery_seq``/``as_of_date`` and is
  never modified here (R-01).

Deliberately NOT here yet (S3c, a separate commit): the Admin version-history
screen (ADM-06/07).
"""
from __future__ import annotations
