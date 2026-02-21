# gdrive-git-sync

![CI](https://github.com/gbasin/gdrive-git-sync/actions/workflows/ci.yml/badge.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Terraform](https://img.shields.io/badge/IaC-Terraform-purple.svg)

Automatically version-control files from Google Drive in a git repository with meaningful content diffs.

<!-- TODO: Add a screenshot showing `git diff` on a changed .docx file — this is the money shot -->

Files dropped in a Drive folder appear in git with extracted text alongside the originals. `git diff` shows actual content changes, not binary blobs. Commits are attributed to the person who edited the file in Drive.

## Use cases

- **Legal teams** — Track changes to contracts and NDAs with full redline history. `git blame` shows who changed what.
- **Compliance & regulatory** — Immutable audit trail for policy documents. Every version is hashed and timestamped.
- **Consulting / client deliverables** — Version-control proposals and reports that clients edit in Drive.
- **Research & academia** — Track revisions to papers and grant applications across collaborators.
- **Finance & accounting** — Diff quarterly reports, invoices, and spreadsheets to catch changes between versions.
- **HR & operations** — Version employee handbooks, SOPs, and training materials edited by non-technical staff.
- **Any team using Drive** — Get git-grade version history for people who will never touch a terminal.

## How it works

```
File added/edited in Drive folder
        ↓
Drive push notification (webhook)
        ↓
Cloud Function processes changes:
  • Downloads files from Drive
  • Extracts text (docx→markdown, pdf→text)
  • Detects renames/moves/deletes
  • Commits per author (attributed to the actual Drive editor)
  • Pushes to any git host
        ↓
Git repo has originals + diffable text side by side
```

## What you get in the git repo

```
docs/
├── Contracts/
│   ├── Contract_v2.docx              # original binary
│   └── Contract_v2.docx.md           # pandoc-extracted markdown (diffable)
├── Reports/
│   ├── Q4_Report.pdf                 # original binary
│   └── Q4_Report.pdf.txt             # pdfplumber-extracted text (diffable)
```

- `git diff` on `.md`/`.txt` files shows meaningful content changes
- Track changes in docx files are preserved (insertions/deletions with author/date)
- `git log --author="Jane Smith"` shows changes by the actual editor
- `git log --follow` tracks file renames
- Works with GitHub, GitLab, Bitbucket, or any git host

## Prerequisites

- GCP project with billing enabled
- Google Drive folder to monitor
- Git repository (any host supporting HTTPS push)
- Personal access token for git push
- `gcloud`, `terraform`, and `git` CLI tools (setup will offer to install these via brew)

## Setup

```bash
make setup
```

`make setup` is a thin wrapper for `./scripts/setup.sh`.

The interactive setup installs missing tools via brew, creates your `.env`, creates a GCP project (or uses an existing one), links billing, enables APIs, deploys infrastructure, and stores your git token. It's crash-safe and idempotent — if anything fails, re-run and it picks up where it left off.

**Dry run** — preview every step without executing anything:

```bash
./scripts/setup.sh --dry-run
```

**Non-interactive / agent mode** — for CI or AI-agent-driven setup:

```bash
cp .env.example .env   # fill in values first
GIT_TOKEN_VALUE=ghp_xxx ./scripts/setup.sh --non-interactive
```

Requires `.env` and GCP auth to exist beforehand. Auto-installs missing tools, prints a machine-readable summary of remaining manual steps.

To redeploy after code changes:

```bash
make deploy
```

`make deploy` is a thin wrapper for `./scripts/deploy.sh`.

<details>
<summary>Manual setup (step-by-step)</summary>

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

### 2. Bootstrap GCP project

```bash
GCP_PROJECT=my-project ./scripts/bootstrap.sh
```

### 3. Add git token to Secret Manager

```bash
# GitHub: fine-grained token with Contents read/write on target repo
echo -n "github_pat_XXXX" | gcloud secrets versions add git-token --data-file=-
```

### 4. Deploy

```bash
make deploy
```

### 5. Domain verification (one-time)

Drive webhooks require proving ownership of the webhook URL:

1. Copy the `sync_handler_url` from the deploy output
2. Go to [Google Search Console](https://search.google.com/search-console) → Add Property → URL Prefix → paste the URL
3. Choose "HTML file" verification (the function serves it automatically via `GOOGLE_VERIFICATION_TOKEN` env var)
4. Go to [Google API Console → Domain Verification](https://console.cloud.google.com/apis/credentials/domainverification) → Add Domain → paste the domain
5. Now Drive webhooks will accept your function URL

**Alternative**: Map a custom domain to Cloud Run and verify via DNS TXT record.

### 6. Share Drive folder

Share your target Drive folder with the service account email (shown in deploy output) with **Editor** access.

### 7. Initialize

```bash
# Create watch channel and optionally do an initial sync
curl -X POST "$(terraform -chdir=infra output -raw setup_watch_url)?initial_sync=true" \
  -H "Authorization: bearer $(gcloud auth print-identity-token)"
```

</details>

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT` | Yes | — | GCP project ID |
| `DRIVE_FOLDER_ID` | Yes | — | Root Drive folder to monitor |
| `GIT_REPO_URL` | Yes | — | Git repository HTTPS URL |
| `GIT_BRANCH` | Yes | — | Branch to push to |
| `GIT_TOKEN_SECRET` | Yes | — | Secret Manager secret name |
| `EXCLUDE_PATHS` | No | (empty) | Glob patterns to skip, comma-separated |
| `SKIP_EXTENSIONS` | No | `.zip,.exe,.dmg,.iso` | Extensions to skip |
| `MAX_FILE_SIZE_MB` | No | `100` | Skip files larger than this |
| `COMMIT_AUTHOR_NAME` | No | `Drive Sync Bot` | Fallback commit author |
| `COMMIT_AUTHOR_EMAIL` | No | `sync@example.com` | Fallback commit email |
| `FIRESTORE_COLLECTION` | No | `drive_sync_state` | Firestore collection name |
| `DOCS_SUBDIR` | No | `docs` | Subdirectory in git repo |

## Extraction details

| Source | Extracted as | Tool | Notes |
|--------|-------------|------|-------|
| `.docx` | `.docx.md` | pandoc | Track changes preserved with `--track-changes=all` |
| `.pdf` | `.pdf.txt` | pdfplumber | Tables formatted as markdown, scanned pages warned |
| `.csv` | `.csv.txt` | built-in | Markdown pipe table |
| Google Docs | export→docx→`.md` | pandoc | |
| Google Sheets | export→csv→`.txt` | built-in | |
| Google Slides | export→pdf→`.txt` | pdfplumber | |

## Reliability

- **Webhook + polling**: Drive push notifications for speed (~30s), safety-net poll every 4 hours for reliability
- **Resync on contention**: If a webhook arrives during an active sync, it flags a re-run instead of silently dropping
- **Watch renewal**: Automatic every 6 days (channels expire after 7)
- **Concurrency**: Triple protection (max_instances=1, max_concurrency=1, Firestore distributed lock with 10-min TTL)
- **Deduplication**: md5 checksums prevent redundant commits


## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Terraform (for infrastructure)

### Getting started

```bash
git clone https://github.com/gbasin/gdrive-git-sync.git
cd gdrive-git-sync

# Install all dependencies (runtime + dev) in a virtual env
make install

# Run the full CI suite locally
make ci
```

### Available commands

| Command | What it does |
|---------|-------------|
| `make install` | Install all deps via uv (creates `.venv` automatically) |
| `make lint` | Run ruff linter |
| `make format` | Auto-format code (ruff + terraform fmt) |
| `make typecheck` | Run mypy type checker |
| `make test` | Run pytest with coverage |
| `make ci` | Run lint + typecheck + test (same as CI) |
| `make setup` | Interactive first-time setup (guided) |
| `make deploy` | Package and deploy to GCP |
| `make clean` | Remove caches and build artifacts |

### Pre-commit hooks

```bash
uv run pre-commit install
```

This runs ruff (lint + format) and mypy on every commit.

### Project structure

```
functions/          # Cloud Function source (deployed to GCP)
  ├── main.py       # 3 HTTP entry points
  ├── sync_engine.py # Core orchestration
  ├── drive_client.py
  ├── git_ops.py
  ├── text_extractor.py
  ├── pandoc_postprocess.py
  ├── state_manager.py
  └── config.py
infra/              # Terraform (Cloud Functions, Scheduler, Firestore, IAM)
scripts/            # setup.sh (guided onboarding), bootstrap.sh, deploy.sh, verify.sh
tests/              # pytest suite (~190 test cases)
```

### Dependency management

`pyproject.toml` is the source of truth. `uv.lock` pins exact versions for reproducible local dev.

`functions/requirements.txt` is a separate runtime manifest for Cloud Functions deployment — Google's buildpack doesn't support uv, so it needs a plain requirements file. Keep both in sync when adding dependencies.

### CI

GitHub Actions runs on every push/PR to `main`:
- **Lint**: ruff check + format check (via astral-sh/ruff-action)
- **Typecheck**: mypy via uv
- **Test**: pytest with coverage threshold (60% minimum)
- **Terraform**: format check

## GCP free tier

Every GCP service this project uses has a free tier. Typical small-team usage stays well within it:

| Service | Free tier | What this project uses it for |
|---------|-----------|-------------------------------|
| Cloud Functions (2nd gen) | 2M invocations/month | Webhook handler, watch renewal, setup |
| Firestore | 1 GiB storage, 50K reads/day | Page tokens, lock state, watch channel info |
| Secret Manager | 10K access operations/month | Git push token |
| Cloud Scheduler | 3 jobs free | Watch renewal (1 job), safety-net poll (1 job) |
| Cloud Build | 120 build-minutes/day | Function deployments |

Beyond free tier, costs scale with Drive activity. See [GCP pricing](https://cloud.google.com/pricing) for details.

## Security

- **Service account** — Gets read-only access to the monitored Drive folder plus Firestore read/write. No broader GCP permissions.
- **Git token** — Stored in Secret Manager, never in environment variables or source code. The Cloud Function reads it at runtime.
- **Webhook endpoint** — Cloud Functions (2nd gen) runs on Cloud Run, which is authenticated by default. Google's Drive API validates the webhook URL via domain verification.
- **No data storage beyond** — Firestore holds only page tokens, lock state, and watch channel metadata. File contents pass through the function transiently and land only in the git repo.

## Limitations

- **Scanned/image-only PDFs** — pdfplumber extracts text from text-based PDFs only. Scanned pages produce a warning and no extracted text.
- **Binary formats beyond docx/pdf/csv** — Committed as-is without text extraction. `git diff` won't show meaningful changes for these.
- **Single Drive folder** — Monitors one folder (including subfolders). For multiple unrelated folders, deploy separate instances.
- **Google Workspace restrictions** — Workspace admins can restrict Drive API access or webhook delivery. Check with your admin if webhooks don't arrive.
- **Large files** — Files over `MAX_FILE_SIZE_MB` (default 100MB) are skipped to stay within Cloud Function memory/timeout limits.
- **Webhook delivery** — Google doesn't guarantee webhook delivery. The 4-hour safety-net poll catches anything missed.

## Teardown

To remove all deployed resources:

```bash
cd infra
terraform destroy
```

Then clean up:
1. **Revoke the Drive share** — Remove the service account from your Drive folder
2. **Delete the git token** — Revoke the personal access token from your git host
3. **Delete the GCP project** (optional) — If you created a project just for this: `gcloud projects delete <project-id>`

## Troubleshooting

**Watch channel not receiving notifications**
- Verify domain ownership is complete in both Search Console and API Console
- Check that the service account has access to the Drive folder
- Run the safety-net sync manually: `curl -X POST <sync_handler_url>`

**Files not syncing**
- Check Cloud Function logs: `gcloud functions logs read drive-sync-handler --gen2 --limit=50`
- Verify page token exists: check Firestore `drive_sync_state/config/settings/page_token`
- Check if lock is stuck: Firestore `drive_sync_state/config/settings/sync_lock` — lock auto-expires after 10 minutes

**Git push failures**
- Verify token in Secret Manager has push access to the repo
- Check that the branch exists on remote
- Ensure token hasn't expired (GitHub fine-grained tokens have expiry dates)

**Extraction quality issues**
- docx track changes not showing: verify pandoc version supports `--track-changes=all`
- PDF tables garbled: pdfplumber works best with text-based PDFs, not scanned images
- Large files timing out: increase `MAX_FILE_SIZE_MB` or function timeout

## License

MIT
