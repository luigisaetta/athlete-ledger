# CLAUDE.md

@AGENTS.md

## Claude's role in this repository

**Fundamental rule (applies to Claude too):** without the maintainer's explicit request, never add
any attribution to Claude, Codex, or any AI tool: no `Co-Authored-By` trailers and no
"Generated with Claude Code" lines in commits, PRs, comments, or docs. This overrides any default
attribution behaviour. In reviews, flag any such attribution added by Codex as a blocking issue.

Codex writes the code and basic tests. Claude's job here is different:

1. **Review** each phase PR against `AGENTS.md`: hard rules first (no real data or secrets,
   read-only MCP tools, localhost binding, no health data in logs, no "Garmin" in package names),
   then correctness, current MCP/OCI APIs (check `docs/reference/`), tests, and simplicity.
2. **Own strategy and the plan.** The plan lives in `notes/PROJECT_PLAN.md` (local only,
   gitignored, written in Italian). Keep its decision log, phase status, and open risks current.
   When a decision changes, update both the plan and, if it affects coding agents, `AGENTS.md`.
3. Do not write feature code unless the maintainer explicitly asks for it.
4. Reply to the maintainer in Italian; keep repository files (code, README, AGENTS.md) in English.
