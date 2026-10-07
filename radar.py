#!/usr/bin/env python3
"""Opportunity Radar: collects scholarships, conferences, AI/CV papers and articles,
scores and summarizes them with an LLM, sends a Telegram digest, and writes
docs/items.json for the web dashboard.

Env vars (set as GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID      -> Telegram delivery (optional)
  GEMINI_API_KEY  or  ANTHROPIC_API_KEY     -> LLM summaries (optional; keyword fallback)
  LLM_MODEL                                 -> override model name (optional)
  GITHUB_TOKEN                              -> higher GitHub API rate limit (optional)
Flags:
  --dry-run   don't send Telegram, print the digest instead
  --no-llm    skip the LLM, use keyword scoring
"""
import argparse, datetime as dt, hashlib, html, json, os, re, sys, time
from pathlib import Path
from urllib.parse import quote

import feedparser, requests, yaml

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "items.json"
UA = {"User-Agent": "OpportunityRadar/1.0 (+personal digest bot)"}
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def uid(*parts):
    return hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:16]


def clean(text, n=600):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:n]


def iso(struct):
    try:
        return dt.datetime(*struct[:6], tzinfo=dt.timezone.utc).isoformat()
    except Exception:
        return NOW.isoformat()


def get(url, **kw):
    r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=30, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- sources
def src_rss(s, cat):
    feed = feedparser.parse(get(s["url"]).content)
    out = []
    for e in feed.entries[:40]:
        link = e.get("link", "")
        out.append(dict(id=uid(link or e.get("title")), category=cat, source=s["name"],
                        title=clean(e.get("title"), 300), url=link,
                        published=iso(e.get("published_parsed") or e.get("updated_parsed") or NOW.timetuple()),
                        raw=clean(e.get("summary") or e.get("description"))))
    return out


def src_arxiv(s, cat):
    url = ("https://export.arxiv.org/api/query?search_query=" + quote(s["query"]) +
           f"&sortBy=submittedDate&sortOrder=descending&max_results={s.get('max', 30)}")
    feed = feedparser.parse(get(url).content)
    out = []
    for e in feed.entries:
        link = e.get("link", "")
        aid = link.rsplit("/", 1)[-1].split("v")[0]
        out.append(dict(id=uid("arxiv", aid), category=cat, source=s["name"],
                        title=clean(e.get("title"), 300), url=link,
                        published=iso(e.get("published_parsed") or NOW.timetuple()),
                        raw=clean(e.get("summary"))))
    return out


def src_hf_daily(s, cat):
    out = []
    for row in get(s["url"]).json():
        p = row.get("paper", row)
        pid = p.get("id") or row.get("id")
        if not pid:
            continue
        out.append(dict(id=uid("arxiv", str(pid)), category=cat, source=s["name"],
                        title=clean(p.get("title") or row.get("title"), 300),
                        url=f"https://huggingface.co/papers/{pid}",
                        published=row.get("publishedAt") or p.get("publishedAt") or NOW.isoformat(),
                        raw=clean(p.get("summary") or row.get("summary")),
                        upvotes=p.get("upvotes", 0)))
    return out


def _parse_deadline(d):
    s = str(d.get("date") or d.get("deadline") or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


def src_ai_deadlines(s, cat):
    hdr = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.getenv("GITHUB_TOKEN") else {}
    files = [f for f in get(s["url"], headers=hdr).json() if f.get("name", "").endswith((".yml", ".yaml"))]
    want = {t.lower() for t in s.get("tags", [])}
    out = []
    for f in files:
        try:
            entries = yaml.safe_load(get(f["download_url"]).text) or []
        except Exception as ex:
            log("  skip", f.get("name"), ex)
            continue
        for c in entries if isinstance(entries, list) else [entries]:
            tags = {str(t).lower() for t in (c.get("tags") or c.get("sub") or [])}
            if want and not (tags & want):
                continue
            dls = c.get("deadlines") or ([{"date": c.get("deadline"), "label": "Deadline"}] if c.get("deadline") else [])
            future = sorted((d for d in ((_parse_deadline(x), x.get("label", "Deadline")) for x in dls) if d[0]),)
            future = [d for d in future if d[0] >= TODAY.isoformat()]
            if not future:
                continue
            nxt = future[0]
            title = f"{c.get('title', '')} {c.get('year', '')}".strip()
            where = ", ".join(str(x) for x in (c.get("city"), c.get("country")) if x) or c.get("place", "")
            out.append(dict(id=uid("conf", title), category=cat, source=s["name"], title=title,
                            url=c.get("link", ""), published=NOW.isoformat(), deadline=nxt[0],
                            raw=clean(f"{c.get('full_name', '')}. {nxt[1]}: {nxt[0]}. "
                                      f"Conference: {c.get('date', '')} {where}. Tags: {', '.join(sorted(tags))}. {c.get('note', '')}")))
    return out


SOURCES = {"rss": src_rss, "arxiv": src_arxiv, "hf_daily": src_hf_daily, "ai_deadlines": src_ai_deadlines}


def collect(cfg):
    items = []
    for cat, srcs in cfg["sources"].items():
        kws = [k.lower() for k in cfg.get("keywords", {}).get(cat, [])]
        for s in srcs:
            try:
                got = SOURCES[s["type"]](s, cat)
            except Exception as ex:
                log(f"! {s['name']}: {ex}")
                continue
            if kws:
                got = [i for i in got if any(k in (i["title"] + " " + i["raw"]).lower() for k in kws)]
            log(f"  {s['name']}: {len(got)}")
            items += got
            time.sleep(1)  # be polite (arXiv asks for this)
    return items


# ---------------------------------------------------------------- LLM
PROMPT = """You screen opportunities and research for this person:
{profile}

For EACH item below return a JSON object with:
  "id": same id,
  "score": 0-10 relevance/value for this person. Scholarships: high only if FULLY funded
           and open to Nigerians/Africans and relevant to AI/CV/engineering/data; 0-3 for jobs,
           unrelated fields, or partial funding. Conferences: high for top CV/ML/robotics
           venues, Africa-based events, or ones with travel grants. Papers/articles: high
           for computer vision, edge/mobile AI, robotics, smart systems, practical tutorials.
  "summary": 1-2 plain sentences on what it is and why it matters to them,
  "fully_funded": true/false/null (scholarships only, else null),
  "deadline": "YYYY-MM-DD" if a deadline is stated, else null.
Return ONLY a JSON array.

ITEMS:
{items}"""


def _call_llm(text):
    model = os.getenv("LLM_MODEL")
    if os.getenv("GEMINI_API_KEY"):
        model = model or "gemini-2.5-flash"
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": os.environ["GEMINI_API_KEY"]}, timeout=120,
            json={"contents": [{"parts": [{"text": text}]}],
                  "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}})
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if os.getenv("ANTHROPIC_API_KEY"):
        model = model or "claude-haiku-4-5"
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120,
                          headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                   "anthropic-version": "2023-06-01"},
                          json={"model": model, "max_tokens": 8000,
                                "messages": [{"role": "user", "content": text}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])
    return None


def _json_array(s):
    m = re.search(r"\[.*\]", s, re.S)
    return json.loads(m.group(0)) if m else []


KW_HI = ["fully funded", "computer vision", "masters", "msc", "scholarship", "object detection",
         "segmentation", "edge", "mobile", "on-device", "robot", "yolo", "opencv", "cvpr", "iccv",
         "eccv", "neurips", "africa", "nigeria", "deep learning"]


def keyword_score(i):
    t = (i["title"] + " " + i["raw"]).lower()
    return min(10, 3 + sum(k in t for k in KW_HI))


def score(items, cfg, use_llm=True):
    pending = [i for i in items if "score" not in i]
    if not pending:
        return
    has_llm = use_llm and (os.getenv("GEMINI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))
    for b in range(0, len(pending), 20):
        batch = pending[b:b + 20]
        res = {}
        if has_llm:
            listing = "\n".join(f'- id={i["id"]} [{i["category"]}] {i["title"]} :: {i["raw"][:400]}' for i in batch)
            try:
                out = _call_llm(PROMPT.format(profile=cfg["profile"], items=listing))
                res = {r["id"]: r for r in _json_array(out or "") if isinstance(r, dict) and "id" in r}
            except Exception as ex:
                log("! LLM failed, using keywords:", ex)
        for i in batch:
            r = res.get(i["id"])
            if r:
                i["score"] = int(r.get("score") or 0)
                i["summary"] = r.get("summary") or i["raw"][:200]
                i["fully_funded"] = r.get("fully_funded")
                i["deadline"] = i.get("deadline") or r.get("deadline")
            else:
                i["score"] = keyword_score(i)
                i["summary"] = i["raw"][:220]
                i.setdefault("deadline", None)
                i["fully_funded"] = ("fully funded" in (i["title"] + i["raw"]).lower()) if i["category"] == "scholarships" else None
        time.sleep(2)


# ---------------------------------------------------------------- Telegram
ICON = {"scholarships": "🎓", "conferences": "🗓", "papers": "📄", "articles": "📰"}
LABEL = {"scholarships": "Scholarships", "conferences": "Conferences & CFPs",
         "papers": "AI / CV Papers", "articles": "Articles & Tutorials"}


def build_digest(new, closing):
    e = html.escape
    lines = [f"<b>📡 Opportunity Radar — {TODAY:%a %d %b %Y}</b>"]
    if closing:
        lines.append("\n<b>⏰ Closing within 14 days</b>")
        for i in closing:
            lines.append(f'• <a href="{e(i["url"])}">{e(i["title"])}</a> — <b>{i["deadline"]}</b>')
    for cat in LABEL:
        group = [i for i in new if i["category"] == cat]
        if not group:
            continue
        lines.append(f"\n<b>{ICON[cat]} {LABEL[cat]}</b>")
        for i in group:
            tag = " 💰 Fully funded" if i.get("fully_funded") else ""
            dl = f" · ⏳ {i['deadline']}" if i.get("deadline") else ""
            lines.append(f'• <a href="{e(i["url"])}">{e(i["title"])}</a> ({i["score"]}/10{dl}){tag}\n  <i>{e(i.get("summary", ""))}</i>')
    return "\n".join(lines)


def send_telegram(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        log("Telegram not configured; skipping send.")
        return False
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) > 3800:
            chunks.append(cur); cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for c in chunks:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=30,
                          json={"chat_id": chat, "text": c, "parse_mode": "HTML",
                                "disable_web_page_preview": True})
        if not r.ok:
            log("! Telegram:", r.text)
            return False
    return True


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    store = json.loads(DATA.read_text())["items"] if DATA.exists() else []
    known = {i["id"] for i in store}

    log("Collecting…")
    fresh = [i for i in collect(cfg) if i["id"] not in known]
    seen = set()
    fresh = [i for i in fresh if not (i["id"] in seen or seen.add(i["id"]))]
    for i in fresh:
        i["first_seen"] = NOW.isoformat()
    log(f"{len(fresh)} new items; scoring…")
    score(fresh, cfg, use_llm=not a.no_llm)
    store = fresh + store

    cutoff = (NOW - dt.timedelta(days=cfg.get("keep_days", 60))).isoformat()
    store = [i for i in store if i["first_seen"] >= cutoff or (i.get("deadline") or "") >= TODAY.isoformat()]

    to_send = sorted((i for i in store if not i.get("sent") and i["score"] >= cfg["min_score"]),
                     key=lambda i: -i["score"])[:cfg["max_per_digest"]]
    soon = (TODAY + dt.timedelta(days=14)).isoformat()
    closing = [i for i in store if i.get("sent") and not i.get("reminded") and i["category"] == "scholarships"
               and i.get("deadline") and TODAY.isoformat() <= i["deadline"] <= soon]

    if to_send or closing:
        digest = build_digest(to_send, closing)
        ok = True
        if a.dry_run:
            print(digest)
        else:
            ok = send_telegram(digest)
        if ok and not a.dry_run:
            for i in to_send: i["sent"] = True
            for i in closing: i["reminded"] = True
    else:
        log("Nothing new above threshold.")

    DATA.parent.mkdir(exist_ok=True)
    site = cfg.get("site") or {}
    DATA.write_text(json.dumps({"updated": NOW.isoformat(),
                                "site": {"telegram": site.get("telegram_link") or None},
                                "items": store}, ensure_ascii=False, indent=1))
    log(f"Saved {len(store)} items -> {DATA.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
