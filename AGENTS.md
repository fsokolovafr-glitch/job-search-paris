# Agent instructions

This is a public repository. Never commit a CV, contact details, application history,
interview notes, rejection history, private messages, API keys, or user-provided source
exports. Personal files belong under `private/`, which is ignored by Git.

## Before work

1. Read `README.md`, `docs/acceptance.md`, and the relevant script/tests.
2. For a vacancy or application request, also read
   `.claude/skills/job-application/SKILL.md`. These instructions apply to Codex as
   well as Claude; do not assume a Claude-only tool exists.
3. Treat missing sources and missing private tracker data as `UNKNOWN`. Never infer
   “not applied” from an empty public repository.

## Data boundaries

- `data/job-search.db`, public JSON, dashboard and reports contain only companies,
  vacancies, provenance and monitoring results.
- The application tracker defaults to `private/applications.json` or the path in
  `JOB_APPLICATIONS_FILE`; it must remain untracked.
- Resolve the master CV in this order: a file supplied in the current session,
  the private path in `JOB_MASTER_CV` (including an ignored local `.env`), then
  another explicit user-provided private path. The normal local source may be a
  separate private Obsidian vault. Never commit that vault path or its contents.
  Treat `.env` as local configuration, not as evidence that the referenced file exists.
- Use `scripts/sanitize_public_snapshot.py` before importing a reviewed private
  export. Public snapshots must have an empty `applications` list.

## Acceptance

Run the commands in `docs/acceptance.md`. Verify both the requested behavior and
the privacy scan. Local tests do not prove that GitHub Actions ran or that discovery
found new jobs. Sending an application, publishing a branch, or opening a pull
request requires an explicit user request and must be verified at the destination.
