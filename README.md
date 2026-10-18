# GitHub Auto-Fork Telegram Bot

A small Python bot that runs as a **scheduled GitHub Actions workflow**. It watches GitHub users/organizations and forks public repositories created after you started watching them. It also accepts owner-management commands in Telegram.

No paid VPS or always-on phone is required. It is scheduled every five minutes, but GitHub may delay scheduled jobs. This is polling, not instant event delivery.

## Features

- Watch multiple GitHub usernames or organizations.
- Telegram commands: `/addowner USER`, `/removeowner USER`, `/list`, `/status`, `/help`, `/whoami`.
- Edit `config.json` directly to add/remove owners too.
- Automatically fork newly created public repositories into the GitHub account belonging to `GH_FORK_TOKEN`.
- Avoids repeatedly trying the same repository by storing IDs in `config.json`.
- Only the Telegram numeric user ID configured in Actions secrets can use management commands.
- Uses GitHub Actions secrets for credentials.

## 1. Create the repository

1. Create a **public** GitHub repository, e.g. `github-auto-fork-bot`.
2. Upload all files from this project to its default branch. Keep `.github/workflows/auto-fork.yml` at that exact path.
3. In the repository, open **Settings → Actions → General → Workflow permissions** and allow read and write permissions. The workflow also declares `contents: write`.
4. Make sure the workflow file is on the default branch. Scheduled workflows only run from the default branch.

## 2. Create the GitHub token

Create a **classic personal access token** at GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic).

- Select only the `public_repo` scope.
- Do not select `repo`, `workflow`, or unrelated scopes.
- Copy it once and add it as a repository Actions secret named `GH_FORK_TOKEN`.

This token can write to your public repositories, so treat it like a password. Never paste it into `config.json`, source files, Telegram, or logs. If you prefer a fine-grained token, the GitHub fork endpoint requires the relevant repository permissions for forking (including repository creation write and contents read, or administration write and contents read); token availability across source repositories can be trickier, so the classic `public_repo` setup is the simpler route for public repos.

## 3. Create the Telegram bot

1. Open Telegram and chat with the official `@BotFather`.
2. Send `/newbot` and follow its prompts.
3. Copy the bot token and add it as repository Actions secret `TELEGRAM_BOT_TOKEN`.
4. Open a chat with your new bot and press **Start**.
5. Run the workflow once using **Actions → GitHub Auto-Fork → Run workflow**.
6. Send `/whoami` to the bot. It replies with your numeric Telegram user ID.
7. Add that number as repository Actions secret `TELEGRAM_ALLOWED_USER_ID`.
8. Run the workflow again. Now the ID is authorized to use commands.

If `/whoami` does not reply, check that the workflow ran successfully and the bot token secret is correct. The bot checks Telegram updates only when the workflow runs.

## 4. Add the secrets

In your repository go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|---|---|
| `GH_FORK_TOKEN` | GitHub classic PAT with `public_repo` scope |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | Your numeric Telegram user ID |

Do not put secrets in repository variables or in `config.json`.

## 5. Start watching accounts

Once the secrets are set, run the workflow manually once or wait for the schedule.

In Telegram, send:

- `/addowner Clash-Projects` - start watching. Existing repos are not bulk-forked.
- `/removeowner Clash-Projects` - stop watching.
- `/list` - list monitored owners.
- `/status` - show the target GitHub account and owner count.
- `/help` - show commands.

The account name may be a GitHub user or organization. Do not include a GitHub URL; just the username/org name.

## 6. Configure it by editing `config.json`

You can also edit `config.json` on the default branch. Example:

```json
{
  "owners": {
    "Clash-Projects": {
      "added_at": null,
      "seen_repo_ids": []
    }
  },
  "telegram_update_offset": 0
}
```

- `added_at: null` means the next run sets the baseline to the current time and skips old repositories.
- To monitor repositories created after a specific UTC time, set `added_at` to an ISO-8601 UTC timestamp, e.g. `"2026-10-18T00:00:00Z"`.
- `seen_repo_ids` is managed by the bot. Do not clear it unless you intentionally want the bot to reconsider older repositories (it still checks whether the fork already exists).
- `telegram_update_offset` is managed by the bot. Leave it as-is.

Owner keys are case-insensitive in practice, but keep one consistent spelling. A manual config edit takes effect on the next scheduled/manual run.

## 7. Test and troubleshoot

- Open **Actions → GitHub Auto-Fork → Run workflow** to test now.
- Inspect the run log for API permission errors or missing secrets. The script never prints token values.
- `403` from GitHub usually means a token permission, rate-limit, or policy issue.
- `422` when creating a fork can mean a fork already exists or GitHub rejected the request; the bot checks for an existing same-name repository first.
- Fork creation is asynchronous on GitHub. The workflow records a successful `202 Accepted` response.
- GitHub can delay scheduled workflows, and schedules in public repositories may be disabled after 60 days without repository activity. Keep the repo active and check the Actions tab periodically.
- GitHub's terms, rate limits, and anti-abuse systems still apply. This bot deliberately handles only repositories created after an owner was added; it does not mass-fork an owner's entire history.

## Important limitations

- Scheduled workflow cadence is **every five minutes at best**, not guaranteed real-time.
- Telegram commands are processed only when a workflow run starts.
- `GH_FORK_TOKEN` forks into the account that owns that token. The bot does not transfer or mirror source repositories.
- Private repositories are not included.
