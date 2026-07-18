-- Ledger v2 (Wave 3 P0-4, 2026-07-18): Hermes-style completion contracts.
-- Owner: main (Vayu). Target: Agent Nervous System project, schema agents.
--
-- done_when: acceptance criteria stated at task creation. task_ledger.py
--            refuses to close a contracted task without evidence.
-- evidence:  how the contract was met, written at close time. Distinct from
--            `result` (free-form outcome) so a verifier can judge
--            evidence-vs-contract without parsing prose.
--
-- Additive only — old task_ledger.py code runs unchanged against the new
-- schema, so this applies BEFORE the relay restart with zero downtime.
-- Reverse:
--   alter table agents.tasks drop column if exists done_when;
--   alter table agents.tasks drop column if exists evidence;

alter table agents.tasks add column if not exists done_when text;
alter table agents.tasks add column if not exists evidence text;
