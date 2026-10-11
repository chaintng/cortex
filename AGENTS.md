# Cortex

Follow the KaoJai workspace workflow when used from that workspace.

- Cortex owns the Python Mem0 runtime and container. Database contracts remain in
  `db-contracts`; AWS resources remain in `infra`; persistent relay remains in
  `messaging-workers`; KMS is separate.
- Run `uv sync --frozen` and `uv run pytest -q`. Use only disposable migrated local
  databases for `CI_TEST_DATABASE_URL`; never test against tenant or production data.
- Keep source references, authorization, generation validity and model budgets.
  Do not accept raw Mem0 filters or turn customer text into trusted instructions.
- Keep runtime DDL, local SQLite history and telemetry disabled. Never log evidence,
  credentials, model output or connection strings.
- Build Linux AMD64 Lambda images. Publishing images, releasing aliases and enabling
  consumers are separate operations. Preserve current runtime identifiers during
  this repository extraction; do not recreate infrastructure just to rename it.
- The pilot is disabled. Provisioning-only mode is not permission to enable it.
