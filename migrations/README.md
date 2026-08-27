# Migrations

Alembic is the only schema-creation path. `0001_foundation` is the empty, reversible
Stage 1 baseline. `0002_initial_self` adds only the normalized Stage 2 identity,
personality, values and minimal activation-audit tables. Applying migrations never
activates Satori or inserts domain rows.

Stage 3 is intentionally stateless and adds no migration. `0003_conversation_memory`
implements the accepted Stage 4 retention decision with sessions, idempotent interactions,
exact user/assistant messages, episodic memories, exact evidence and terminal formation
decisions. It adds no semantic-memory, embedding/vector, relationship, emotion or user-model
tables. Downgrade returns to the Stage 3 physical head (`0002_initial_self`).
