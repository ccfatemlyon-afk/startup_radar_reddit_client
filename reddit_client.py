#!/usr/bin/env python3
"""Read-only Reddit Data API client for Qidian Radar.

This is the standalone reference client for the access pattern described in
our Data Access Request:
  - OAuth client_credentials, read-only (no write endpoints are called)
  - top posts of the last 24h for a configured list of subreddits
  - up to 10 top-level comments for a small candidate set
  - one request at a time, 429/5xx backoff, descriptive User-Agent

Credentials are never stored in this repository. Provide them through
REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET or a local config.json that is
gitignored.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
DEFAULT_UA = "windows:qidian-radar:v1.0 (by /u/your_reddit_username)"


class RedditError(RuntimeError):
    pass


def load_config(path: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["client_id"] = os.environ.get("REDDIT_CLIENT_ID") or cfg.get("client_id") or ""
    cfg["client_secret"] = os.environ.get("REDDIT_CLIENT_SECRET") or cfg.get("client_secret") or ""
    return cfg


def get_token(client_id: str, client_secret: str, user_agent: str) -> str:
    if not (client_id and client_secret):
        raise RedditError(
            "Set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET or fill config.json"
        )
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        method="POST",
        headers={"User-Agent": user_agent, "Authorization": "Basic " + basic},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise RedditError("token response did not contain access_token")
    return token


def get_json(
    token: str,
    path: str,
    params: dict[str, Any],
    user_agent: str,
    retries: int = 3,
) -> Any:
    url = API_BASE + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent, "Authorization": "Bearer " + token},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = int(float(e.headers.get("Retry-After") or 60))
                time.sleep(wait)
                continue
            if 500 <= e.code < 600 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RedditError(f"GET {path} failed: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RedditError(f"GET {path} failed: {e}") from e
    raise RedditError(f"GET {path} failed after retries")


def fetch_top(
    token: str, sub: str, cfg: dict[str, Any], user_agent: str
) -> list[dict[str, Any]]:
    payload = get_json(
        token,
        f"/r/{urllib.parse.quote(sub, safe='')}/top",
        {
            "t": cfg.get("time_filter", "day"),
            "limit": int(cfg.get("top_limit", 30)),
            "raw_json": 1,
        },
        user_agent,
    )
    posts: list[dict[str, Any]] = []
    for child in (payload.get("data") or {}).get("children") or []:
        d = child.get("data") or {}
        if d.get("stickied"):
            continue
        posts.append(
            {
                "id": d.get("id"),
                "source": "reddit",
                "subreddit": d.get("subreddit"),
                "title": d.get("title"),
                "author": d.get("author"),
                "score": int(d.get("score") or 0),
                "num_comments": int(d.get("num_comments") or 0),
                "created_utc": d.get("created_utc"),
                "permalink": "https://www.reddit.com" + (d.get("permalink") or ""),
                "url": d.get("url"),
                "selftext": (d.get("selftext") or "")[:400],
            }
        )
    return posts


def fetch_comments(
    token: str, sub: str, post_id: str, cfg: dict[str, Any], user_agent: str
) -> list[dict[str, Any]]:
    limit = int(cfg.get("comments_per_post", 10))
    payload = get_json(
        token,
        f"/r/{urllib.parse.quote(sub, safe='')}/comments/{urllib.parse.quote(post_id, safe='')}",
        {"sort": "top", "limit": limit, "raw_json": 1},
        user_agent,
    )
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    comments: list[dict[str, Any]] = []
    for child in (payload[1].get("data") or {}).get("children") or []:
        if child.get("kind") != "t1":
            continue
        d = child.get("data") or {}
        body = (d.get("body") or "").strip()
        if not body or body == "[deleted]":
            continue
        comments.append(
            {
                "author": d.get("author"),
                "score": int(d.get("score") or 0),
                "body": body[:400],
            }
        )
        if len(comments) >= limit:
            break
    return comments


def run(cfg: dict[str, Any]) -> None:
    user_agent = cfg.get("user_agent") or DEFAULT_UA
    subs = [s for s in (cfg.get("subreddits") or []) if s]
    if not subs:
        raise RedditError("config has no subreddits")
    interval = float(cfg.get("request_interval_seconds", 1.0))

    token = get_token(cfg["client_id"], cfg["client_secret"], user_agent)

    posts: list[dict[str, Any]] = []
    for i, sub in enumerate(subs):
        if i:
            time.sleep(interval)
        try:
            items = fetch_top(token, sub, cfg, user_agent)
        except RedditError as e:
            print(f"[warn] r/{sub}: {e}", file=sys.stderr)
            continue
        print(f"[top] r/{sub}: {len(items)} posts")
        posts.extend(items)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for p in posts:
        pid = p.get("id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        unique.append(p)

    unique.sort(
        key=lambda p: (p.get("score", 0), p.get("num_comments", 0)), reverse=True
    )
    candidates = unique[: int(cfg.get("comment_candidate_limit", 30))]

    for i, post in enumerate(candidates):
        if i:
            time.sleep(interval)
        try:
            post["comments"] = fetch_comments(
                token, post["subreddit"], post["id"], cfg, user_agent
            )
        except RedditError as e:
            post["comments"] = []
            print(f"[warn] comments {post['id']}: {e}", file=sys.stderr)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "subreddits": subs,
        "post_count": len(unique),
        "candidates": candidates,
    }
    out_path = cfg.get("output_file") or "output.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(
        f"[done] wrote {out_path}: {len(unique)} posts, "
        f"{len(candidates)} candidates with comments"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only Reddit top/comments client")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="", help="output JSON path")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.out:
        cfg["output_file"] = args.out
    try:
        run(cfg)
    except RedditError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
