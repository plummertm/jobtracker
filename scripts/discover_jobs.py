#!/usr/bin/env python3
"""
Title-first Job Discovery Collector (free-source version)

No company list required.

Sources:
  - Jobicy public API (remote roles; no API key required)
  - Remote OK public JSON feed (remote roles; no API key required)
  - Remotive public API (remote roles; no API key required, ~24h delayed)

The collector searches by configured titles, filters for US eligibility,
deduplicates, and inserts new results into Supabase discovered_jobs.

Required GitHub Actions secrets:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_USER_ID
"""

import os, re, json, html, hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USER_ID = os.environ["SUPABASE_USER_ID"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")

S = requests.Session()
S.headers.update({
    "User-Agent": "ToniJobDiscovery/2.0 (personal job-search dashboard)",
    "Accept": "application/json,text/plain,*/*",
})

def cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def clean_html(v):
    v = html.unescape(v or "")
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.I)
    v = re.sub(r"</p\s*>", "\n", v, flags=re.I)
    v = re.sub(r"<[^>]+>", " ", v)
    return re.sub(r"\s+", " ", v).strip()

def norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def iso_date(v):
    if not v:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else None

def source_key(provider, external_id, company, title, url):
    raw = "|".join(map(str, [provider, external_id or "", company, title, url]))
    return hashlib.sha256(raw.encode()).hexdigest()[:48]

def title_ok(title, c):
    t = norm(title)
    excludes = [norm(x) for x in c.get("exclude_titles", [])]
    if any(x and x in t for x in excludes):
        return False
    targets = [norm(x) for x in c["titles"]]
    return any(x and x in t for x in targets)

def us_ok(location, c):
    if not c.get("us_only", True):
        return True
    l = norm(location)
    if not l:
        return True
    positives = [
        "usa","united states","u.s.","us ","america","north america","northern america",
        "remote","anywhere","worldwide",
        "washington, dc","washington dc","district of columbia",
        "maryland","virginia","arlington","mclean","reston","herndon","alexandria",
        "tysons","fairfax","bethesda"
    ]
    negatives = [
        "canada","united kingdom","uk","europe","india","germany","france","spain","italy",
        "australia","singapore","japan","brazil","mexico","poland","romania","netherlands"
    ]
    if any(x in l for x in positives):
        return True
    if any(x in l for x in negatives):
        return False
    return True

def salary_ok(lo, hi, c):
    floor = c.get("salary_floor")
    if not floor:
        return True
    # Do not reject jobs with missing salary. Reject only when an explicit max is below the floor.
    if hi not in (None, ""):
        try:
            return float(hi) >= float(floor)
        except Exception:
            pass
    return True

def jobicy(c):
    # Query each title separately because Jobicy supports a free text tag filter.
    seen = set()
    for title in c["titles"]:
        params = {"count": min(int(c.get("per_query", 100)), 200), "geo": "usa", "tag": title}
        r = S.get("https://jobicy.com/api/v2/remote-jobs", params=params, timeout=30)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            jid = str(j.get("id") or "")
            if jid in seen:
                continue
            seen.add(jid)
            yield {
                "provider":"jobicy",
                "external_id":jid,
                "company":j.get("companyName") or "",
                "title":j.get("jobTitle") or "",
                "url":j.get("url") or "",
                "location":j.get("jobGeo") or "Remote",
                "posted_date":iso_date(j.get("pubDate")),
                "description":clean_html(j.get("jobDescription") or j.get("jobExcerpt") or ""),
                "salary_min":j.get("salaryMin"),
                "salary_max":j.get("salaryMax"),
                "source_site":"Jobicy",
                "work_arrangement":"Remote",
            }

def remoteok(c):
    r = S.get("https://remoteok.com/api", timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and data and not data[0].get("position"):
        data = data[1:]
    for j in data if isinstance(data, list) else []:
        yield {
            "provider":"remoteok",
            "external_id":str(j.get("id") or ""),
            "company":j.get("company") or "",
            "title":j.get("position") or "",
            "url":j.get("url") or j.get("apply_url") or "",
            "location":j.get("location") or "Remote",
            "posted_date":iso_date(j.get("date")),
            "description":clean_html(j.get("description") or ""),
            "salary_min":j.get("salary_min"),
            "salary_max":j.get("salary_max"),
            "source_site":"Remote OK",
            "work_arrangement":"Remote",
        }

def remotive(c):
    # Public feed is intentionally delayed by Remotive, so this is supplemental.
    r = S.get("https://remotive.com/api/remote-jobs", timeout=30)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        salary_text = str(j.get("salary") or "")
        nums = [int(x.replace(",","")) for x in re.findall(r"\$?\s*([1-9]\d{1,2}(?:,\d{3})+)", salary_text)]
        lo = nums[0] if nums else None
        hi = nums[1] if len(nums) > 1 else None
        yield {
            "provider":"remotive",
            "external_id":str(j.get("id") or ""),
            "company":j.get("company_name") or "",
            "title":j.get("title") or "",
            "url":j.get("url") or "",
            "location":j.get("candidate_required_location") or "Remote",
            "posted_date":iso_date(j.get("publication_date")),
            "description":clean_html(j.get("description") or ""),
            "salary_min":lo,
            "salary_max":hi,
            "source_site":"Remotive",
            "work_arrangement":"Remote",
        }

FETCHERS = {"jobicy": jobicy, "remoteok": remoteok, "remotive": remotive}

def existing_keys():
    headers={"apikey":SUPABASE_KEY,"Authorization":f"Bearer {SUPABASE_KEY}"}
    params={"select":"source_key","user_id":f"eq.{USER_ID}","source_key":"not.is.null","limit":"10000"}
    r=S.get(f"{SUPABASE_URL}/rest/v1/discovered_jobs",headers=headers,params=params,timeout=30)
    r.raise_for_status()
    return {x["source_key"] for x in r.json() if x.get("source_key")}

def insert(rows):
    if not rows:
        return
    headers={
        "apikey":SUPABASE_KEY,
        "Authorization":f"Bearer {SUPABASE_KEY}",
        "Content-Type":"application/json",
        "Prefer":"return=minimal,resolution=ignore-duplicates",
    }
    for i in range(0,len(rows),100):
        r=S.post(f"{SUPABASE_URL}/rest/v1/discovered_jobs",
                 headers=headers,data=json.dumps(rows[i:i+100]),timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase insert failed {r.status_code}: {r.text}")

def main():
    c=cfg()
    keys=existing_keys()
    rows=[]
    scanned=0
    matched=0

    for provider in c.get("providers", ["jobicy","remoteok","remotive"]):
        fetcher=FETCHERS.get(provider)
        if not fetcher:
            print(f"Unknown provider: {provider}")
            continue
        print(f"Searching {provider}...")
        try:
            for j in fetcher(c):
                scanned += 1
                if not title_ok(j["title"], c):
                    continue
                if not us_ok(j["location"], c):
                    continue
                if not salary_ok(j["salary_min"], j["salary_max"], c):
                    continue

                key=source_key(j["provider"],j["external_id"],j["company"],j["title"],j["url"])
                if key in keys:
                    continue

                rows.append({
                    "user_id":USER_ID,
                    "company":j["company"] or "Unknown company",
                    "role":j["title"],
                    "job_url":j["url"] or None,
                    "posted_date":j["posted_date"],
                    "location":j["location"] or None,
                    "work_arrangement":j["work_arrangement"],
                    "salary_min":j["salary_min"],
                    "salary_max":j["salary_max"],
                    "source_site":j["source_site"],
                    "description":j["description"] or None,
                    "external_job_id":j["external_id"] or None,
                    "source_type":j["provider"],
                    "source_key":key,
                    "decision":"new",
                    "first_seen_at":datetime.now(timezone.utc).isoformat(),
                    "last_seen_at":datetime.now(timezone.utc).isoformat(),
                })
                keys.add(key)
                matched += 1
        except Exception as e:
            print(f"  {provider} ERROR: {e}")

    insert(rows)
    print(f"Scanned {scanned} postings; inserted {matched} new title matches.")

if __name__=="__main__":
    main()
