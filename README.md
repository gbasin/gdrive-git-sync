# gdrive-git-sync

Automatically version-control files from Google Drive in a git repository with meaningful content diffs.

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
- `gcloud`, `terraform`, and `git` CLI tools

## Setup

### 1. Bootstrap GCP project

```bash
cp .env.example .env
# Edit .env with your values

GCP_PROJECT=my-project ./scripts/bootstrap.sh
```

### 2. Add git token to Secret Manager

```bash
# GitHub example (fine-grained token with Contents read/write on target repo)
echo -n "github_pat_XXXX" | gcloud secrets versions add git-token --data-file=-
```

### 3. Deploy

```bash
./scripts/deploy.sh
```

### 4. Domain verification (one-time)

Drive webhooks require proving ownership of the webhook URL:

1. Copy the `sync_handler_url` from the deploy output
2. Go to [Google Search Console](https://search.google.com/search-console) → Add Property → URL Prefix → paste the URL
3. Choose "HTML file" verification (the function serves it automatically via `GOOGLE_VERIFICATION_TOKEN` env var)
4. Go to [Google API Console → Domain Verification](https://console.cloud.google.com/apis/credentials/domainverification) → Add Domain → paste the domain
5. Now Drive webhooks will accept your function URL

**Alternative**: Map a custom domain to Cloud Run and verify via DNS TXT record.

### 5. Share Drive folder

Share your target Drive folder with the service account email (shown in deploy output) with **Editor** access.

### 6. Initialize

```bash
# Create watch channel and optionally do an initial sync
curl -X POST "$(terraform -chdir=infra output -raw setup_watch_url)?initial_sync=true" \
  -H "Authorization: bearer $(gcloud auth print-identity-token)"
```

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

## Cost

At typical usage (a few files/day): **~$0.20/month** (Cloud Scheduler jobs). Everything else falls within GCP free tiers.

## Running tests

```bash
pip install -r functions/requirements.txt
pip install pytest
pytest tests/
```

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
