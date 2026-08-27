# ADR-0010: Explicit activation and normalized initial self

- Status: Accepted
- Date: 2026-07-27

## Context

Stage 2 must create one stable Satori identity before any conversation or provider call.
The initial personality and values need a reproducible source, but that source cannot
remain the authority after Satori starts living. Bootstrap, migrations, imports and read
operations must not create or reset identity. The first persistent mutation also needs
provenance and an atomic audit record without prematurely building the general future
audit subsystem.

## Decision

Activation is an explicit `ActivateSatori` application use case. One installation database
has one primary slot. The first activation succeeds; a repeat raises typed
`AlreadyActivated`. The CLI translates that expected outcome into a successful, explicit
no-op message. No import, migration, bootstrap, database open or status read activates
Satori.

The canonical input is the package resource
`satori.resources.seeds/satori-v1.json`. It is strict versioned JSON parsed by a Pydantic
adapter into `InitialSatoriSeed`; raw dictionaries do not cross into the domain. Seed
provenance stores its ID, schema version and SHA-256 of canonical validated JSON. Hashing
is independent of JSON formatting. Seed files are creation inputs only: after activation,
the database is authoritative and later seed changes cannot reseed existing state.

Persistent state is normalized into identity, personality metadata/traits, value
metadata/items and a minimal generic audit-event table. Trait/value keys are validated
lower snake_case records rather than DB columns or an unrestricted JSON blob. Canonical
seed schema v1 requires the exact accepted trait/value key sets, while this representation
does not require a DB migration merely to introduce a future schema-versioned key.

Activation claims the singleton slot with a database uniqueness/check constraint and
stages identity, personality, values and one `satori.activation` audit event in a single
Unit of Work. IDs and UTC activation time come from injected Stage 1 abstractions. Any
failure rolls back all records. Reads return frozen, versioned domain snapshots; Stage 2
provides no personality or value mutation API.

## Consequences

Satori exists independently of process lifetime, provider and prompt. Identity ID,
activation time, initial personality, values and provenance survive runtime reconstruction.
The normalized schema supports bounds and referential constraints and remains inspectable.
Two stale activation contenders cannot both create a primary identity.

Stage 2 supports only SQLite, so the singleton claim adapter uses SQLite conflict handling.
Custom external seeds, import/restore, personality evolution, value evolution and general
audit decisions remain future explicitly gated work. The current immutable snapshot is the
Stage 2 export/read fragment; no second import write path is introduced.

## Alternatives rejected

Automatic startup activation hides lifecycle mutation and can accidentally reset identity.
Treating repeated activation as overwrite violates continuity. Keeping personality only in
the seed makes source files live state. One giant character JSON blob weakens ownership,
constraints and future migrations. ORM models as domain entities leak write access.
Database auto-increment IDs couple identity to one physical database. Generating the seed
with an LLM or embedding it in a prompt violates deterministic activation and the persistent
self boundary.
