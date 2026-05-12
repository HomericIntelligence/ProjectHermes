# scripts/

Helper scripts for ProjectHermes development and CI. Run them from the repo root.

| Script                 | When to run                                                  | What it does                                                                 |
|------------------------|--------------------------------------------------------------|------------------------------------------------------------------------------|
| `check_dep_sync.py`    | CI (and locally before `git push`)                           | Fails if any `[project.dependencies]` entry is missing a `<NEXT_MAJOR` upper bound. |
| `check-symlinks.sh`    | CI (`symlink-check` job in `_required.yml`)                  | Verifies no tracked symlink escapes the repo root and none are broken.       |
| `discover-tech-debt.sh`| Ad-hoc, when triaging tech debt                              | Greps the tree for `FIXME`/`TODO`/`HACK`/`XXX`/`DEPRECATED` markers.         |
| `export-openapi.py`    | After any FastAPI route/schema change (`just export-openapi`)| Regenerates the committed `openapi.json` from the live FastAPI app.          |

Add new scripts here only if they have a clear long-term purpose; one-off shell snippets belong in
the issue or PR that needs them. When you add a script, update this README in the same commit.
