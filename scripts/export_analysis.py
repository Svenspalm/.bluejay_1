#!/usr/bin/env python3
"""Export the local AI analysis (commandcenter) into the dashboard's analysis.json, then optionally push.

  python3 scripts/export_analysis.py --year 2025 --montana ~/LLM/montana           # write only
  python3 scripts/export_analysis.py --year 2027 --montana ~/LLM/montana --push    # write, commit, push

Reads <montana>/analysis.db (2025) or <montana>/data/<year>/analysis.db and synthesis.json from the pipeline repo
(see its DESIGN.md "Running another session"). Writes docs/data/<year>/analysis.json keyed by bill tag.
The dashboard merges this onto the live bills.json, so a bill with no entry here just shows without a summary.
This is the only step that needs the GPU; the 3x-daily Action does not run it.
"""
import argparse, json, os, sqlite3, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AREAS = {"taxation_revenue": "Taxation & Revenue", "state_budget_appropriations": "State Budget & Appropriations",
    "k12_education": "Education K-12", "higher_education": "Higher Education", "health_human_services": "Health Care & Human Services",
    "justice_courts_crime": "Criminal Justice & Courts", "elections_government_operations": "Elections & Government Operations",
    "local_government": "Local Government", "natural_resources_energy_environment": "Natural Resources, Energy & Environment",
    "fish_wildlife_recreation": "Fish, Wildlife & Outdoor Recreation", "agriculture_water": "Agriculture & Water",
    "business_labor_professions": "Business, Labor & Professions", "housing_property_land": "Property, Housing & Land Use",
    "transportation_infrastructure": "Transportation & Infrastructure", "safety_emergency_military": "Public Safety & Emergency",
    "families_children_social": "Families, Children & Social Issues", "legislature_interim_studies": "Legislature & Interim Studies",
    "other": "Other"}


def paths(montana, year):
    base = montana if year == 2025 else os.path.join(montana, "data", str(year))
    return os.path.join(base, "analysis.db"), os.path.join(base, "synthesis.json")


def export(montana, year):
    db, syn = paths(montana, year)
    if not os.path.exists(db):
        raise SystemExit(f"{db} not found: run the pipeline for {year} first")
    out = {}
    for bill, a in sqlite3.connect(db).execute("select bill, analysis from bills where analysis is not null"):
        a = json.loads(a)
        out[bill] = {
            "area": AREAS.get(a.get("policy_area"), a.get("policy_area")),
            "area2": AREAS.get(a.get("secondary_area")) if a.get("secondary_area") not in (None, "none") else None,
            "one_line": a.get("one_line"), "summary": a.get("plain_summary"),
            "changes": a.get("what_changes") or [], "affected": a.get("who_is_affected") or [],
            "fiscal": a.get("fiscal_impact"), "fiscal_dir": a.get("fiscal_direction"),
            "numbers": a.get("key_numbers") or [], "effective": a.get("effective_date"),
            "new_program_or_crime": bool(a.get("creates_new_program_or_crime")), "complexity": a.get("complexity"),
        }
    doc = {"year": year, "model": "gpt-oss:20b (local)", "count": len(out), "bills": out}
    if os.path.exists(syn):
        s = json.load(open(syn)).get("session") or {}
        doc["session"] = {k: s.get(k) for k in ("headline", "overview", "themes", "biggest_fiscal_moves",
                                                 "notable_new_programs_or_crimes", "what_failed_or_was_vetoed", "caveats")}
    dest = os.path.join(ROOT, "docs", "data", str(year), "analysis.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w", encoding="utf-8").write(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print(f"{year}: {len(out)} analysed bills -> {dest} ({os.path.getsize(dest) / 1e6:.1f} MB)")
    return dest


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def push(year):
    git("add", f"docs/data/{year}/analysis.json")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("analysis unchanged; nothing to push")
        return
    git("pull", "--rebase", "--autostash", "origin", "main")
    r = git("commit", "-m", f"analysis: {year} AI summaries refreshed from commandcenter")
    if r.returncode: sys.exit(r.stderr or r.stdout)
    r = git("push", "origin", "main")
    if r.returncode: sys.exit(r.stderr or r.stdout)
    print("pushed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--montana", default=os.path.expanduser("~/LLM/montana"))
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    export(a.montana, a.year)
    if a.push: push(a.year)
