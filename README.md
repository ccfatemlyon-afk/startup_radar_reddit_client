# Qidian Radar — Reddit Read-Only Client

Qidian Radar is a Chinese-language daily digest of public startup and product
discussions. This repository contains the standalone reference client used to
read Reddit's public Data API.

**It is not a Reddit bot.** The client never posts, comments, votes, messages
users, or performs moderation. It only reads public listings and public
comments, and every published digest item links back to the original Reddit
thread.

The full product (LLM summarization, open-source project matching, email and
web rendering) is not part of this repository. This repository documents the
exact API calls, request volume, and data handling used by the production
collector.

## What it does

Once per day:

1. Reads the top posts of the last 24 hours from each configured public
   subreddit.
2. Deduplicates posts by ID, drops stickied posts, and ranks the remainder.
3. Reads up to 10 top-level comments for a small candidate set.
4. Writes a local JSON working file for the digest pipeline.

It calls no write endpoints. It does not access private messages, user
profiles, moderation queues, voting histories, or historical data beyond the
daily top listing.

## API calls and volume

| Call | Endpoint | Volume |
|---|---|---|
| OAuth token | `POST https://www.reddit.com/api/v1/access_token` | 1/day |
| Top posts | `GET https://oauth.reddit.com/r/{subreddit}/top?t=day&limit=30&raw_json=1` | 18/day |
| Comments | `GET https://oauth.reddit.com/r/{subreddit}/comments/{post_id}?sort=top&limit=10&raw_json=1` | up to 30/day |

Typical volume is about 49 requests per day, one request at a time, with a
1-second interval. That is far below Reddit's 100 QPM limit. The client backs
off on HTTP 429/5xx and uses a descriptive User-Agent.

## Data handling

- Only public post/comment text and metadata are read: post ID, subreddit,
  author display name, score, comment count, creation time, permalink,
  external URL, and a short selftext excerpt.
- No user profiles are built. No data is sold, licensed, or redistributed.
- No model training or fine-tuning is performed on Reddit data. An LLM is
  used only to draft short summaries for the current digest.
- `output.json` is a local working file. It is gitignored, is not committed,
  and is intended to be deleted after the digest is produced. The published
  archive contains short summaries and links to the original threads, not a
  raw mirror of Reddit content.
- Comments are capped at 10 per selected post and 400 characters per comment.

## Why not Devvit

Devvit is Reddit's on-platform app platform: apps are installed by moderators
into a specific community and run inside Reddit. This project is an external,
off-platform read-only digest that aggregates public listings across 18
communities we do not moderate. Devvit app access is scoped to the community
where the app is installed, and its runtime is designed for short-lived
on-platform experiences, not a multi-step Python ETL/LLM/email pipeline.
See the Data Access Request for the full explanation.

## Setup

Requires Python 3.9+ and an approved Reddit OAuth app.

```bash
cp config.example.json config.json
# Fill in client_id / client_secret, or use environment variables:
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
python reddit_client.py --config config.json
```

`config.json` is gitignored. Never commit credentials. `config.example.json`
contains placeholders only.

## Output

`output.json` (gitignored) contains:

```text
{
  "generated_at": "...",
  "subreddits": [...],
  "post_count": 0,
  "candidates": [
    {
      "id": "...",
      "source": "reddit",
      "subreddit": "...",
      "title": "...",
      "author": "...",
      "score": 0,
      "num_comments": 0,
      "created_utc": 0,
      "permalink": "https://www.reddit.com/r/.../comments/...",
      "url": "...",
      "selftext": "...",
      "comments": [{"author": "...", "score": 0, "body": "..."}]
    }
  ]
}
```

## Production platform

https://qidian-radar.pages.dev
