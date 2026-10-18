#!/usr/bin/env python3
"""Scheduled Telegram/GitHub auto-fork bot. Secrets are read only from env vars."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

CONFIG_PATH = Path("config.json")
GH_API = "https://api.github.com"
GH_VERSION = "2022-11-28"
HTTP = requests.Session()
HTTP.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": GH_VERSION,
    "User-Agent": "github-actions-auto-fork-bot",
})
TG_BASE = "https://api.telegram.org"
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"owners": {}, "telegram_update_offset": 0}
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    cfg.setdefault("owners", {})
    cfg.setdefault("telegram_update_offset", 0)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    temp = CONFIG_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(CONFIG_PATH)


def github_request(method: str, path: str, **kwargs: Any) -> requests.Response:
    token = os.environ.get("GH_FORK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing Actions secret GH_FORK_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    response = HTTP.request(method, GH_API + path, headers=headers, timeout=25, **kwargs)
    if response.status_code >= 400:
        # Do not print request headers or any secret values.
        body = response.text[:500]
        raise RuntimeError(f"GitHub API {method} {path} returned {response.status_code}: {body}")
    return response


def get_target_username() -> str:
    return github_request("GET", "/user").json()["login"]


def tg_call(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing Actions secret TELEGRAM_BOT_TOKEN")
    response = requests.post(
        f"{TG_BASE}/bot{token}/{method}",
        json=payload or {},
        timeout=25,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram API {method} returned HTTP {response.status_code}")
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API {method} returned ok=false")
    return data


def send_message(chat_id: int, text: str) -> None:
    # Telegram text limit is 4096; keep notifications comfortably below it.
    tg_call("sendMessage", {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True})


def allowed_user_id() -> str:
    return os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()


def normalize_owner(raw: str) -> str | None:
    owner = raw.strip().lstrip("@")
    if not OWNER_RE.fullmatch(owner):
        return None
    return owner


def handle_telegram(cfg: dict[str, Any]) -> bool:
    """Process queued commands. Returns True if config changed."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Telegram token absent; skipping Telegram command polling.")
        return False

    changed = False
    offset = int(cfg.get("telegram_update_offset", 0))
    data = tg_call("getUpdates", {
        "offset": offset,
        "limit": 100,
        "timeout": 0,
        "allowed_updates": ["message"],
    })
    updates = data.get("result", [])
    auth_id = allowed_user_id()

    for update in updates:
        update_id = int(update.get("update_id", 0))
        cfg["telegram_update_offset"] = max(int(cfg.get("telegram_update_offset", 0)), update_id + 1)
        changed = True

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        sender_id = str(sender.get("id", ""))
        body = (message.get("text") or "").strip()
        if not chat_id or not body.startswith("/"):
            continue

        # /whoami is intentionally available before the allow-list secret is configured.
        command_text = body.split()[0].split("@")[0].lower()
        args = body.split()[1:]
        if command_text == "/whoami":
            send_message(chat_id, f"Your Telegram numeric user ID is: {sender_id}\nAdd this as the TELEGRAM_ALLOWED_USER_ID Actions secret.")
            continue

        if not auth_id or sender_id != auth_id:
            # Do not acknowledge unauthorized management commands.
            continue

        if command_text in ("/start", "/help"):
            send_message(chat_id,
                "GitHub Auto-Fork bot\n\n"
                "/addowner USER - watch a GitHub user/org for newly created public repos\n"
                "/removeowner USER - stop watching\n"
                "/list - list watched owners\n"
                "/status - show bot status\n"
                "/help - show commands\n"
                "/whoami - show your Telegram numeric ID\n\n"
                "Checks run on GitHub Actions schedules, so replies and forks may be delayed.")
        elif command_text == "/addowner":
            if not args:
                send_message(chat_id, "Usage: /addowner USER")
                continue
            owner = normalize_owner(args[0])
            if not owner:
                send_message(chat_id, "That username/org format is invalid. Send only the GitHub username or org name.")
                continue
            key = next((k for k in cfg["owners"] if k.lower() == owner.lower()), owner)
            if key in cfg["owners"]:
                send_message(chat_id, f"Already watching {key}.")
                continue
            cfg["owners"][key] = {"added_at": utc_now(), "seen_repo_ids": []}
            changed = True
            send_message(chat_id, f"Now watching {owner}. Repositories created before this command will not be bulk-forked.")
        elif command_text == "/removeowner":
            if not args:
                send_message(chat_id, "Usage: /removeowner USER")
                continue
            owner = normalize_owner(args[0])
            key = next((k for k in cfg["owners"] if owner and k.lower() == owner.lower()), None)
            if key is None:
                send_message(chat_id, "That owner is not on your watch list.")
            else:
                del cfg["owners"][key]
                changed = True
                send_message(chat_id, f"Stopped watching {key}.")
        elif command_text == "/list":
            owners = sorted(cfg["owners"], key=str.lower)
            send_message(chat_id, "Watching:\n" + ("\n".join(f"• {name}" for name in owners) if owners else "(none yet)"))
        elif command_text == "/status":
            try:
                target = get_target_username()
            except Exception as exc:
                target = f"(GitHub auth error: {exc})"
            send_message(chat_id, f"Target GitHub account: {target}\nOwners watched: {len(cfg['owners'])}\nSchedule: every 5 minutes when GitHub Actions runs.")
        else:
            send_message(chat_id, "Unknown command. Send /help.")
    return changed


def destination_repo_exists(target: str, name: str) -> dict[str, Any] | None:
    response = HTTP.get(
        f"{GH_API}/repos/{target}/{name}",
        headers={"Authorization": f"Bearer {os.environ['GH_FORK_TOKEN'].strip()}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": GH_VERSION},
        timeout=25,
    )
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub API checking destination repo {target}/{name} returned {response.status_code}")
    return response.json()


def fork_repo(repo: dict[str, Any], target: str) -> tuple[str, str]:
    source = repo["full_name"]
    existing = destination_repo_exists(target, repo["name"])
    if existing:
        parent = existing.get("parent") or {}
        if existing.get("fork") and parent.get("full_name", "").lower() == source.lower():
            return "exists", existing.get("html_url", f"https://github.com/{target}/{repo['name']}")
        return "name-conflict", existing.get("html_url", f"https://github.com/{target}/{repo['name']}")

    response = github_request("POST", f"/repos/{source}/forks", json={})
    if response.status_code == 202:
        payload = response.json()
        return "created", payload.get("html_url", f"https://github.com/{target}/{repo['name']}")
    # github_request raises on errors; this is defensive.
    raise RuntimeError(f"Unexpected fork status {response.status_code} for {source}")


def monitor_owners(cfg: dict[str, Any]) -> bool:
    changed = False
    target = get_target_username()
    print(f"Authenticated target account: {target}")
    for owner, state in list(cfg["owners"].items()):
        state.setdefault("seen_repo_ids", [])
        if state.get("added_at") is None:
            # Config-file owners with no baseline begin watching from now,
            # rather than unexpectedly forking their full history.
            state["added_at"] = utc_now()
            changed = True
            print(f"Initialized baseline for {owner}: {state['added_at']}")
            continue

        try:
            baseline = parse_time(state["added_at"])
            profile = github_request("GET", f"/users/{owner}").json()
            if profile.get("type") == "Organization":
                endpoint = f"/orgs/{owner}/repos"
                params_base = {"type": "public", "sort": "created", "direction": "desc", "per_page": 100}
            else:
                endpoint = f"/users/{owner}/repos"
                params_base = {"type": "owner", "sort": "created", "direction": "desc", "per_page": 100}

            seen = {str(x) for x in state["seen_repo_ids"]}
            new_candidates: list[dict[str, Any]] = []
            for page in range(1, 21):
                params = dict(params_base, page=page)
                batch = github_request("GET", endpoint, params=params).json()
                if not batch:
                    break
                stop = False
                for repo in batch:
                    created_at = parse_time(repo["created_at"])
                    if created_at <= baseline:
                        stop = True
                        continue
                    if repo.get("private") or str(repo["id"]) in seen:
                        continue
                    new_candidates.append(repo)
                if stop or len(batch) < 100:
                    break

            # Oldest first makes notifications easier to follow if several were created.
            new_candidates.sort(key=lambda item: item["created_at"])
            for repo in new_candidates:
                repo_id = str(repo["id"])
                try:
                    result, url = fork_repo(repo, target)
                    state["seen_repo_ids"].append(repo_id)
                    seen.add(repo_id)
                    changed = True
                    if result == "created":
                        note = f"Fork accepted: {repo['full_name']}\n{url}"
                        print(note.replace("\n", " | "))
                    elif result == "exists":
                        note = f"Already forked: {repo['full_name']}\n{url}"
                        print(note.replace("\n", " | "))
                    else:
                        note = f"Skipped name conflict: {repo['full_name']}\nYour repo with that name: {url}"
                        print(note.replace("\n", " | "))
                    notify_admin(note)
                except Exception as exc:
                    # Do not mark it seen, so a later run can retry transient errors.
                    print(f"Could not fork {repo['full_name']}: {exc}")
                    notify_admin(f"Fork failed for {repo['full_name']}\n{exc}")
                    time.sleep(1)
        except Exception as exc:
            print(f"Could not monitor {owner}: {exc}")
            notify_admin(f"Could not monitor {owner}\n{exc}")
    return changed


def notify_admin(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    user_id = allowed_user_id()
    if not token or not user_id:
        return
    try:
        tg_call("sendMessage", {
            "chat_id": int(user_id),
            "text": message[:3900],
            "disable_web_page_preview": True,
        })
    except Exception as exc:
        print(f"Telegram notification failed: {exc}")


def main() -> int:
    try:
        cfg = load_config()
        changed = handle_telegram(cfg)
        # Save state before doing GitHub API work so command updates are not lost
        # if a later API call fails. Workflow commit step persists the final file.
        if changed:
            save_config(cfg)
        if os.environ.get("GH_FORK_TOKEN", "").strip():
            if monitor_owners(cfg):
                changed = True
        else:
            print("GH_FORK_TOKEN absent; skipping repository monitoring.")
        if changed:
            save_config(cfg)
        return 0
    except Exception as exc:
        print(f"Fatal error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
