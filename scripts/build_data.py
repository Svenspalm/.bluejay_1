#!/usr/bin/env python3
"""Pull live bill data from api.legmt.gov and write the files the dashboard reads.

  python3 scripts/build_data.py                 # every live session in docs/data/sessions.json
  python3 scripts/build_data.py --year 2027     # one session (even if not marked live)
  python3 scripts/build_data.py --from-raw FILE --year 2025   # rebuild from a saved API response (no network)

Writes, per session:
  docs/data/<year>/bills.json    committed only when it changes (the refresh workflow checks `git diff`)
  docs/data/status.json          when each session was last fetched; generated each run, never committed

Standard library only, so the Action needs no pip install. Exit 0 on success, 1 on any fetch/parse failure
(the previous bills.json is left untouched, so a failed refresh never publishes half a file).
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
API = "https://api.legmt.gov"
HDR = {"Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
       "Origin": "https://bills.legmt.gov", "Referer": "https://bills.legmt.gov/",
       "User-Agent": "montana-legislature-dashboard/1.0 (public-records dashboard; github.com/Svenspalm)"}


def http(url, body=None, tries=4):
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=HDR, method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))
            print(f"  retry {i + 1} after {e}", file=sys.stderr)


def outcome_of(b):
    """Same rules as the analysis pipeline (montana/analyze.py), plus 'In progress' for live bills."""
    st = b["draft"].get("billStatuses") or []
    last = st[-1]["billStatusCode"]["name"] if st else ""
    if b.get("sessionLawChapterNumber"): return "Enacted"
    if "Veto Override" in last: return "Enacted"
    if "Vetoed" in last: return "Vetoed"
    if "Filed with Secretary of State" in last: return "Adopted (resolution)"
    if "Withdrawn" in last: return "Withdrawn"
    if any(w in last for w in ("Died", "Tabled", "Missed Deadline", "Postponed")): return "Failed"
    if not b.get("billNumber"): return "Draft request"
    return "In progress"


def person(leg):
    if not leg: return "", ""
    name = leg.get("displayName") or f"{leg.get('firstName') or ''} {leg.get('lastName') or ''}".strip()
    party = leg.get("politicalParty")
    return name, (party.get("code") if isinstance(party, dict) else str(party or ""))


def day(ts):
    return (ts or "")[:10]


def normalize(raw, legislators, year):
    legs = {l["id"]: l for l in legislators}
    rows = []
    for b in raw["content"]:
        d = b["draft"]
        sts = d.get("billStatuses") or []
        last = sts[-1] if sts else None
        numbered = bool(b.get("billNumber") and b.get("billType"))
        sponsor, party = person(legs.get(b.get("sponsorId")))
        row = {
            "bill": f"{b['billType']['code']}{b['billNumber']}" if numbered else None,
            "draft": d.get("draftNumber"),
            "type": b["billType"]["code"] if numbered else None,
            "number": b.get("billNumber") if numbered else None,
            "title": d.get("shortTitle") or "",
            "chamber": (b.get("billType") or {}).get("chamber"),
            "sponsor": sponsor, "party": party,
            "outcome": outcome_of(b),
            "status": last["billStatusCode"]["name"] if last else "",
            "status_date": day(last["timeStamp"]) if last else day(d.get("date")),
            "chapter": b.get("sessionLawChapterNumber"),
            "subjects": [s["subjectCode"]["description"] for s in (d.get("subjects") or [])],
            "fiscal_note": bool(d.get("fiscalNote")), "legal_note": bool(d.get("legalNote")),
        }
        if numbered:  # full history only once a bill exists; drafts carry just their latest status
            row["history"] = [[day(s["timeStamp"]), s["billStatusCode"]["name"]] for s in sts]
        rows.append(row)
    rows.sort(key=lambda r: (r["type"] is None, r["type"] or "", r["number"] or 0, r["draft"] or ""))
    numbered_n = sum(1 for r in rows if r["bill"])
    return {"year": year, "drafts": len(rows), "numbered": numbered_n, "bills": rows}


def write_if_changed(path, obj):
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    open(tmp, "w", encoding="utf-8").write(text)
    os.replace(tmp, path)
    return True


def build(sess, raw=None, legislators=None, fetched=None):
    year = sess["year"]
    if raw is None:
        print(f"{year}: fetching bills (session id {sess['id']})", file=sys.stderr)
        raw = http(f"{API}/bills/v1/bills/search?includeCounts=true&sort=billType.code,desc&sort=billNumber,asc"
                   f"&sort=draft.draftNumber,asc&limit=9999&offset=0", {"sessionIds": [sess["id"]]})
    if legislators is None:
        legislators = http(f"{API}/legislators/v1/legislators")
    if not raw.get("content"):
        raise SystemExit(f"{year}: API returned no bills; refusing to overwrite the last good file")
    out = normalize(raw, legislators, year)
    if not sess.get("live"):  # a finished session: drafts that never became bills are noise
        out["bills"] = [r for r in out["bills"] if r["bill"]]
        out["drafts"] = len(out["bills"])
        if fetched: out["fetched"] = fetched  # committed with the data: status.json is not committed and only covers live sessions
    changed = write_if_changed(os.path.join(DATA, str(year), "bills.json"), out)
    print(f"{year}: {out['drafts']} drafts, {out['numbered']} numbered bills, "
          f"{'updated' if changed else 'unchanged'}", file=sys.stderr)
    return out, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--from-raw", help="saved bills-search response; skips the bills call")
    ap.add_argument("--legislators", help="saved legislators response; skips that call too")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(DATA, "sessions.json")))
    todo = [s for s in cfg["sessions"] if (s["year"] == a.year if a.year else s.get("live"))]
    if not todo:
        raise SystemExit("no matching sessions")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {}
    for s in todo:
        raw = json.load(open(a.from_raw)) if a.from_raw else None
        legs = json.load(open(a.legislators)) if a.legislators else None
        # a rebuild from a saved response is stamped with that file's age, not "now"
        when = datetime.fromtimestamp(os.path.getmtime(a.from_raw), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if a.from_raw else now
        out, changed = build(s, raw, legs, fetched=when)
        status[str(s["year"])] = {"fetched": when, "drafts": out["drafts"], "numbered": out["numbered"], "changed": changed}
    # merge into any existing status so a one-session run does not drop the others
    sp = os.path.join(DATA, "status.json")
    prev = json.load(open(sp)) if os.path.exists(sp) else {"sessions": {}}
    prev["sessions"].update(status)
    prev["generated"] = now
    open(sp, "w").write(json.dumps(prev, indent=1))


if __name__ == "__main__":
    main()
