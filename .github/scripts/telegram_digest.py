#!/usr/bin/env python3
"""Build per-family Telegram digest messages from a workflow run's jobs.

Input : the run's jobs JSON (actions API, all pages merged) on stdin.
Output: one line per kernel family, TAB-separated:  <topic_id>\t<html>
        The caller posts each line to that topic via sendMessage.

Routing (topic IDs in the Wild Kernels Testing forum, editable in place):
  build-android12-5-10 -> 3   (5.10.x-android12)
  build-android13-5-10 -> 5   (5.10.x-android13)
  build-android13-5-15 -> 7   (5.15.x-android13; shares the 5.15 topic)
  build-android14-5-15 -> 7   (5.15.x-android14)
  build-android14-6-1  -> 9   (6.1.x-android14)
  build-android15-6-6  -> 13  (6.6.x-android15)
  build-android16-6-12 -> 15  (6.12.x-android16, incl. the lts sublevel)
  build-android17-6-18 -> 17  (6.18.x-android17, placeholder family)
Unmapped families fall back to topic 3 so nothing is silently dropped.

Job names for callable-workflow runs look like:
  build-android15-6-6 (next, KernelSU-Next, 956ea0f5...) / build (...) / 6.6.82-android15-2025-04 (KernelSU-Next)
The first segment identifies the family job; the last carries the
sublevel/os tag and the root flavor.

Usage: telegram_digest.py --run-id ID --branch BR --event EV
                          --run-url URL --nightly-url URL < jobs.json
"""
import argparse
import html
import json
import sys

TOPICS = {
    "build-android12-5-10": "3",
    "build-android13-5-10": "5",
    "build-android13-5-15": "7",
    "build-android14-5-15": "7",
    "build-android14-6-1": "9",
    "build-android15-6-6": "13",
    "build-android16-6-12": "15",
    "build-android17-6-18": "17",
}
FALLBACK_TOPIC = "3"

# Excluded helper jobs that share the build- prefix
EXCLUDED = {"build-nomount-module"}

FAMILY_NAMES = {
    "build-android12-5-10": "5.10.x-android12",
    "build-android13-5-10": "5.10.x-android13",
    "build-android13-5-15": "5.15.x-android13",
    "build-android14-5-15": "5.15.x-android14",
    "build-android14-6-1": "6.1.x-android14",
    "build-android15-6-6": "6.6.x-android15",
    "build-android16-6-12": "6.12.x-android16",
    "build-android17-6-18": "6.18.x-android17",
}

MARKS = {"success": "✅", "failure": "❌", "cancelled": "⛔",
         "skipped": "⏭️", "startup_failure": "❌"}

MAX_LEN = 4000  # Telegram cap is 4096; leave headroom for entities


def load_jobs(stream) -> list:
    """Accept a single JSON object/array OR gh api --paginate's
    concatenated pages; merge every 'jobs' array found."""
    text = stream.read()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data.get("jobs", [])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    jobs, idx, n = [], 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        obj, end = dec.raw_decode(text, idx)
        idx = end
        if isinstance(obj, dict) and "jobs" in obj:
            jobs.extend(obj["jobs"])
        elif isinstance(obj, list):
            jobs.extend(obj)
    return jobs


def family_of(job_name: str) -> str:
    return job_name.split(" / ")[0].split(" (")[0].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--nightly-url", required=True)
    args = ap.parse_args()

    data = load_jobs(sys.stdin)
    jobs = data if isinstance(data, list) else []
    if not jobs:
        print("telegram-digest: no jobs in input", file=sys.stderr)
        return 1

    fams = {}
    for j in jobs:
        name = j.get("name") or ""
        fam = family_of(name)
        if not fam.startswith("build-android") or fam in EXCLUDED:
            continue  # resolve-sources, nomount, rej, release, ...
        e = fams.setdefault(fam, {"sublevels": [], "flavors": [],
                                  "counts": {}})
        last = name.split(" / ")[-1]
        if " / " in name and "(" in last:
            tag = last.rsplit(" (", 1)[0]
            flavor = last.rsplit(" (", 1)[1].rstrip(")")
            if tag and tag not in e["sublevels"]:
                e["sublevels"].append(tag)
            if flavor and flavor not in e["flavors"]:
                e["flavors"].append(flavor)
        c = j.get("conclusion") or j.get("status") or "unknown"
        e["counts"][c] = e["counts"].get(c, 0) + 1

    # A family whose every job was skipped (single-family dispatch gates
    # the rest off) carries no information - drop it instead of posting
    # an empty card into its topic.
    fams = {f: e for f, e in fams.items()
            if any(k != "skipped" for k in e["counts"])}

    icon = "🌙" if args.event == "schedule" else "🔨"
    for fam, e in sorted(fams.items()):
        topic = TOPICS.get(fam, FALLBACK_TOPIC)
        fname = FAMILY_NAMES.get(fam, fam)
        esc = html.escape

        counts = "  ".join(f"{MARKS.get(k, '•')} {v}"
                           for k, v in sorted(e["counts"].items()))
        subs = sorted(e["sublevels"])
        sub_text = esc(", ".join(subs)) if subs else "—"
        if len(sub_text) > 900:
            sub_text = esc(f"{len(subs)} sublevels") + " (" + \
                esc(", ".join(subs[:6])) + ", …)"
        flavors = esc(", ".join(sorted(e["flavors"]))) if e["flavors"] else "—"

        msg = (
            f"<b>{icon} Wild Kernels — {esc(fname)}</b>\n"
            f"<b>Branch</b>: <code>{esc(args.branch)}</code>  •  "
            f"<b>Trigger</b>: {esc(args.event)}\n"
            f"<b>Run</b>: <a href=\"{esc(args.run_url)}\">#{args.run_id}</a>\n"
            f"<b>Sublevels</b>: {sub_text}\n"
            f"<b>Flavors</b>: {flavors}\n"
            f"<b>Jobs</b>: {counts}\n"
            f"<b>Download</b>: <a href=\"{esc(args.nightly_url)}\">"
            f"nightly.link — all artifacts</a>"
        )
        if len(msg) > MAX_LEN:
            msg = msg[:MAX_LEN - 1] + "…"
        # Records are separated by ASCII RS (\x1e): the HTML itself is
        # multi-line, so the caller splits on RS and reads topic\thtml.
        print(f"{topic}\t{msg}", end="\x1e")

    return 0


if __name__ == "__main__":
    sys.exit(main())
