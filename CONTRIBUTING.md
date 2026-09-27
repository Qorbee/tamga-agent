# Contributing

How Git is used here: branches, commits and pull requests. Kept deliberately light.

## Workflow — GitHub Flow

`main` is always deployable. Every change happens on a short-lived branch off `main`, goes through a
pull request, and merges back. No long-lived `develop` or `release` branches.

1. Branch off the latest `main`.
2. Keep the branch focused: one logical change per branch and PR.
3. Before merging, make every commit a complete step with a Conventional Commit message — no `wip` or
   `fixup` noise (`git rebase -i main` to tidy).
4. Open a PR into `main`; review before merge.
5. Merge with a **merge commit**, not a squash: the branch's shape stays visible in history, and a
   clean branch keeps `main` readable. Delete the branch after merging.

Protect `main`: no direct pushes; changes land only through a reviewed PR.

## Branch names

`type/short-description`, lowercase and hyphenated. The types match the commit types.

```
feat/order-export
fix/login-token-expiry
chore/upgrade-fastapi
docs/architecture-jobs
refactor/extract-pricing-service
test/order-service-edge-cases
```

| Type | For |
|---|---|
| `feat/` | New functionality |
| `fix/` | Bug fixes |
| `chore/` | Tooling, dependencies, config |
| `docs/` | Documentation only |
| `refactor/` | Restructuring without a behaviour change |
| `test/` | Adding or fixing tests |
| `hotfix/` | Urgent production fix, once there is a production |

## Commit messages — Conventional Commits

```
<type>(<optional scope>): <imperative summary>

<optional body — the why, not the what>

<optional footer — BREAKING CHANGE: ..., Refs: <issue-id>>
```

```
feat(orders): add CSV export with column selection
fix(auth): refresh the token before it expires, not after
docs(adr): record the choice of Next.js for the frontend
```

No AI attribution trailers: a commit's author is the human who made it.

## Who runs Git

**A human runs every Git operation.** Coding agents edit the working tree and say when a change is
ready to commit; branching, staging, committing, pushing and PRs are done by the human. This is the
root `AGENTS.md` "Git and commits" rule.
