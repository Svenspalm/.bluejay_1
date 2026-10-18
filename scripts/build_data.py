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
import argparse, json, os, re, sys, time, urllib.request, urllib.error
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
    """Outcome from the final status. A veto that was polled for override but not overridden stays Vetoed:
    the API marks those `VETO_NOT_OVERRIDDEN` and they get no session-law chapter. (The first pipeline version
    counted every "Veto Override" status as Enacted, which put 24 vetoed 2025 bills in the enacted column.)"""
    st = b["draft"].get("billStatuses") or []
    last = st[-1]["billStatusCode"]["name"] if st else ""
    result = (st[-1].get("result") or "") if st else ""
    if b.get("sessionLawChapterNumber"): return "Enacted"
    if "Veto Override" in last: return "Enacted" if result == "VETO_OVERRIDDEN" else "Vetoed"
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


# ---- archive format (2023 and earlier): POST /archive/v1/bills/search {"sessionId": <20231>} ----
def outcome_archive(b, last):
    if b.get("sessionLawChapterNumber") or last == "Chapter Number Assigned": return "Enacted"
    if "Veto" in last: return "Vetoed"          # "Vetoed by Governor", "Veto Override Failed in Legislature"
    if "Filed with Secretary of State" in last: return "Adopted (resolution)"
    if "Withdrawn" in last: return "Withdrawn"
    if any(w in last for w in ("Died", "Tabled", "Missed Deadline", "Postponed")): return "Failed"
    return "In progress" if b.get("billNumber") else "Draft request"


def tidy_subject(s):
    """Archive subjects read 'Health Care Services (see also: Health)' and 'Professions and Occupations  Generally'."""
    return " ".join(re.sub(r"\s*\(see also:[^)]*\)\s*$", "", s).split())


def normalize_archive(raw, year):
    rows = []
    for b in raw["bills"]["content"]:
        if not b.get("billNumber"): continue    # archived sessions: drafts that never became bills are noise
        acts = sorted(b.get("billActions") or [], key=lambda a: a["date"])   # the API lists newest first
        last = acts[-1] if acts else None
        lastname = last["actionType"]["description"] if last else ""
        role = (b.get("primarySponsorBillRoles") or [{}])[0]
        ent = role.get("lawEntity") or {}
        leg = role.get("legislator") or {}
        party = (leg.get("politicalParty") or {}).get("partyCode") or ""
        typ = b["billType"]
        ch = b.get("sessionLawChapterNumber")
        rows.append({
            "bill": f"{typ}{b['billNumber']}", "draft": b["id"]["billDraftNumber"], "type": typ, "number": b["billNumber"],
            "title": b.get("shortTitle") or "",
            "chamber": "HOUSE" if typ.startswith("H") else "SENATE",
            "sponsor": f"{ent.get('firstName') or ''} {ent.get('lastName') or ''}".strip(), "party": party,
            "outcome": outcome_archive(b, lastname), "status": lastname, "status_date": day(last["date"]) if last else "",
            "chapter": int(ch) if ch and str(ch).isdigit() else None,
            "subjects": [tidy_subject(s["subject"]["description"]) for s in (b.get("subjects") or [])],
            "fiscal_note": bool(b.get("hasFiscalNote")), "legal_note": False,
            "history": [[day(a["date"]), a["actionType"]["description"]] for a in acts],
        })
    rows.sort(key=lambda r: (r["type"], r["number"]))
    return {"year": year, "drafts": len(rows), "numbered": len(rows), "bills": rows}


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
    archive = sess.get("api") == "archive"
    if raw is None:
        print(f"{year}: fetching bills (session id {sess['id']}{', archive API' if archive else ''})", file=sys.stderr)
        if archive:
            raw = http(f"{API}/archive/v1/bills/search?limit=9999&offset=0", {"sessionId": sess["id"]})
        else:
            raw = http(f"{API}/bills/v1/bills/search?includeCounts=true&sort=billType.code,desc&sort=billNumber,asc"
                       f"&sort=draft.draftNumber,asc&limit=9999&offset=0", {"sessionIds": [sess["id"]]})
    if archive:
        if not (raw.get("bills") or {}).get("content"):
            raise SystemExit(f"{year}: archive API returned no bills; refusing to overwrite the last good file")
        out = normalize_archive(raw, year)
    else:
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
    set_default(cfg)


def set_default(cfg):
    """The page opens on the newest session that has real bills, not on one that only has draft requests.
    When a new session numbers its first bill this flips by itself on the next refresh."""
    for s in sorted(cfg["sessions"], key=lambda s: -s["year"]):
        p = os.path.join(DATA, str(s["year"]), "bills.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                if json.load(f).get("numbered"):
                    if cfg.get("default") != s["year"]:
                        cfg["default"] = s["year"]
                        with open(os.path.join(DATA, "sessions.json"), "w") as out:
                            out.write(json.dumps(cfg, indent=2) + "\n")
                        print(f"default session is now {s['year']}", file=sys.stderr)
                    return


if __name__ == "__main__":
    main()
