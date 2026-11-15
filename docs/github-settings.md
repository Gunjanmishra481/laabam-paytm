# GitHub settings to change by hand (security plan S4)

These need repository admin rights, so they are not in code. Repository: `Gunjanmishra481/laabam-paytm`.
Each item says where it is in the GitHub web UI.

## 1. Protect `main` (Settings → Rules → Rulesets → New branch ruleset)

- Name: `main`. Enforcement: **Active**. Target branches: **Include default branch**.
- **Restrict deletions**: on.
- **Block force pushes**: on. (Turn it off only for a one-off history fix, like removing a leaked key, then back on.)
- **Require linear history**: on.
- **Require a pull request before merging**: on.
  - Required approvals: **0** while you are the only developer (GitHub doesn't let you approve your own PR); raise
    it to 1 when a second person joins.
  - **Dismiss stale approvals when new commits are pushed**: on.
  - **Require conversation resolution before merging**: on.
- **Require status checks to pass**: on, with **Require branches to be up to date before merging**. Add these
  checks (they appear in the list after one run of each workflow):
  - `backend` (ruff, pip-audit, pytest on SQLite and Postgres)
  - `frontend` (npm audit, lint, builds)
  - `secrets` (gitleaks over the whole history)
  - `e2e` (Playwright against the sandbox API)
  - `image` (hardened image build, smoke test, Trivy, SBOM)
  - `infra` (CDK tests, cdk-nag, synth)
  - `analyze (python)`, `analyze (javascript-typescript)`, `analyze (actions)` (CodeQL)
- **Require code scanning results** (CodeQL, "High or higher"): on, once CodeQL has run once.
- Bypass list: leave **empty**.

After this, changes reach `main` only through pull requests: Claude Code will push a branch and open a PR with
`gh pr create` instead of pushing to `main`.

## 2. Security features (Settings → Code security)

- **Dependency graph**: on. **Dependabot alerts**: on. **Dependabot security updates**: on.
  (Version updates are already configured in `.github/dependabot.yml`.)
- **Secret scanning** and **Push protection**: on. Push protection blocks a push that contains a key, which would
  have stopped the `.env` commits made through the web editor.
- **Code scanning**: CodeQL runs from `.github/workflows/codeql.yml`; keep "Default setup" **off** so the two don't
  clash. Note: on a **private** repository owned by a personal account, code scanning and secret scanning need
  GitHub Advanced Security (or a public repository). If the `analyze` jobs fail with "Advanced Security must be
  enabled", either make the repository public for the submission or tell Claude Code to keep CodeQL only for
  public visibility.

## 3. Actions (Settings → Actions → General)

- **Actions permissions**: "Allow Gunjanmishra481, and select non-Gunjanmishra481, actions and reusable
  workflows", and allow: `actions/checkout@*`, `actions/setup-python@*`, `actions/setup-node@*`,
  `actions/upload-artifact@*`, `github/codeql-action/*@*`. (The workflows pin each one to a commit SHA.)
- **Require actions to be pinned to a full-length commit SHA**: on.
- **Workflow permissions**: **Read repository contents and packages permissions**. Leave "Allow GitHub Actions to
  create and approve pull requests" **off**.
- **Fork pull request workflows**: require approval for all outside collaborators.

## 4. The `production` environment (Settings → Environments → New environment)

Needed in S5, before any deploy; the AWS deploy role trusts only jobs in this environment.

- Name: `production` (exactly; the role's trust policy checks `repo:Gunjanmishra481/laabam-paytm:environment:production`).
- **Required reviewers**: you. **Prevent self-review**: off while you are the only reviewer.
- **Deployment branches and tags**: "Selected branches and tags", add `main` only.
- Environment variable (not a secret; it is not sensitive): `AWS_DEPLOY_ROLE_ARN` = the `DeployRoleArn` output of
  the `LaabamGitHubDeployRole` stack, after S5 deploys it.
- No AWS access keys anywhere: GitHub's OIDC token is exchanged for 1-hour credentials.

## 5. Your account

- Two-factor authentication on (github.com → Settings → Password and authentication), with a passkey or an
  authenticator app.
- Don't edit `.env` or other key files in the GitHub web editor: it commits them. Keys live only in
  `backend/.env` on your computer (git-ignored).
