#!/usr/bin/env python3
"""Weekly refresh of events.json for the Studio Borges welcome book.

Runs on GitHub Actions (see update-events.yml). Asks Claude (with web search) for
real upcoming events in Buenos Aires, validates the result, and only overwrites
events.json if everything checks out. If anything fails, the old file stays.
"""
import datetime
import json
import os
import re
import sys
import urllib.request
import zoneinfo

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")  # free key from Google AI Studio (no card needed)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
EVENTS_PATH = os.environ.get("EVENTS_PATH", "events.json")

if not API_KEY and not GEMINI_KEY:
    sys.exit("Set GEMINI_API_KEY (free) or ANTHROPIC_API_KEY as a repository secret.")

TODAY = datetime.datetime.now(zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")).date()
END = TODAY + datetime.timedelta(weeks=6)
CATEGORIES = {"music", "culture", "sports"}
REQUIRED = ["name", "date", "location", "time", "time_es", "icon", "category", "description", "description_es"]

PROMPT = f"""Today is {TODAY.isoformat()} (Buenos Aires). You maintain events.json for the guest welcome book of an apartment in Palermo Soho, Buenos Aires.

Use web search to find REAL, confirmed events in Buenos Aires from today through {END.isoformat()} (about 6 weeks). Good sources: indiehoy.com monthly and weekly agendas, meteored.com.ar festival guides, movistararena.com.ar, buenosaires.gob.ar/descubrir, timeout.com/es/buenos-aires, bairessecreta.com. Prefer Palermo and nearby venues, but include major concerts and festivals anywhere in the city.

Return 15-25 events:
- Do NOT include recurring weekly or daily events (tours, markets, weekly shows). Only dated one-off events.
- Confirmed one-off events: concerts (Movistar Arena, Luna Park, Niceto Club, C Art Media, stadiums), festivals, museum exhibitions currently on view, sports events, seasonal city events (Christmas, New Year's Eve, etc).
- NEVER invent names, dates or venues. If unsure, leave it out. Drop anything that ended before today.

Output ONLY a JSON array (no prose, no code fences). Each object has these fields:
  "name", "date" (YYYY-MM-DD, first date; ),
  "until" (OPTIONAL YYYY-MM-DD last date; REQUIRED for multi-day events),
  "location", "time" (English), "time_es" (Spanish), "icon" (one emoji),
  "category" ("music" | "culture" | "sports"), "description" (1-2 sentences, English), "description_es" (same in Spanish)."""


def call(messages):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 16000,
        "messages": messages,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "content-type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def run_gemini():
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "tools": [{"google_search": {}}],
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "content-type": "application/json", "x-goog-api-key": GEMINI_KEY})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.load(r)
    parts = resp["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        sys.exit("No JSON array found in Gemini output:\n" + text[:500])
    return json.loads(text[start:end + 1])


def run():
    if GEMINI_KEY:
        return run_gemini()
    messages = [{"role": "user", "content": PROMPT}]
    for _ in range(6):  # handle pause_turn continuations
        resp = call(messages)
        if resp.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": resp["content"]})
            continue
        break
    text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        sys.exit("No JSON array found in model output:\n" + text[:500])
    return json.loads(text[start:end + 1])


def valid_date(s):
    return isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) and datetime.date.fromisoformat(s)


def clean(events):
    out = []
    for e in events:
        if not isinstance(e, dict) or any(not e.get(k) for k in REQUIRED):
            continue
        if e["category"] not in CATEGORIES or not valid_date(e["date"]):
            continue
        if "until" in e and not valid_date(e["until"]):
            e.pop("until")
        if "days" in e and not (isinstance(e["days"], list) and all(isinstance(d, int) and 0 <= d <= 6 for d in e["days"])):
            e.pop("days")
        if (e.get("until") or e["date"]) < TODAY.isoformat():
            continue
        out.append(e)
    return out


def main():
    events = clean(run())
    one_offs = [e for e in events if not e.get("days")]
    if len(events) < 10 or len(one_offs) < 5:
        sys.exit(f"Refusing to overwrite events.json: only {len(events)} valid events ({len(one_offs)} one-off).")
    events.sort(key=lambda e: (e["date"], e["name"]))
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {len(events)} events ({events[0]['date']} -> {max((e.get('until') or e['date']) for e in events)}).")


if __name__ == "__main__":
    main()
