#!/usr/bin/env python3
"""
Title-first automatic job discovery using FREE web search + DIRECT employer ATS links only.

How it works:
1) Searches Bing RSS and DuckDuckGo HTML for configured job titles restricted to:
   - jobs.ashbyhq.com
   - jobs.lever.co
   - job-boards.greenhouse.io
   - boards.greenhouse.io
   - jobs.smartrecruiters.com
2) Rejects LinkedIn, Indeed, ZipRecruiter, Appcast, Glassdoor, etc.
3) Opens each direct ATS page and extracts title/company/location where possible.
4) Filters against your title/location preferences.
5) Deduplicates with source_key.
6) Inserts new jobs into Supabase discovered_jobs.

No paid API and no manual company list.
"""

import os, re, json, html, hashlib, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, List, Dict

import requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USER_ID = os.environ["SUPABASE_USER_ID"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/153.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

DIRECT_ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "jobs.smartrecruiters.com": "smartrecruiters",
}
BLOCKED_HOST_FRAGMENTS = [
    "linkedin.", "indeed.", "ziprecruiter.", "glassdoor.", "appcast.",
    "monster.", "careerbuilder.", "simplyhired.", "talent.com", "jooble."
]

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def clean(v):
    return re.sub(r"\s+", " ", html.unescape(str(v or ""))).strip()

def host_of(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""

def canonical_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        # Strip tracking/query params; ATS job ID lives in path.
        return urllib.parse.urlunsplit((p.scheme or "https", p.netloc.lower(), p.path.rstrip("/"), "", ""))
    except Exception:
        return url

def is_direct_ats(url):
    h = host_of(url)
    if any(x in h for x in BLOCKED_HOST_FRAGMENTS):
        return False
    return h in DIRECT_ATS_HOSTS

def source_type(url):
    return DIRECT_ATS_HOSTS.get(host_of(url), "direct")

def title_matches(title, cfg):
    t = norm(title)
    if any(norm(x) in t for x in cfg.get("exclude_titles", [])):
        return False
    return any(norm(x) in t for x in cfg.get("titles", []))

def location_matches(location, cfg):
    if not cfg.get("us_only", True):
        return True
    l = norm(location)
    if not l:
        return True
    negatives = [
        "united kingdom"," uk","canada","toronto","vancouver","europe","emea",
        "india","germany","france","spain","italy","australia","singapore",
        "japan","brazil","mexico","poland","romania","netherlands","ireland"
    ]
    positives = [
        "united states","usa","u.s.","remote","us remote","north america",
        "washington, dc","washington dc","district of columbia","maryland","virginia",
        "arlington","mclean","reston","herndon","alexandria","tysons","fairfax","bethesda"
    ]
    if any(x in l for x in positives):
        return True
    if any(x in l for x in negatives):
        return False
    return True

def key_for(url):
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:48]

def unwrap_search_url(url):
    """Unwrap DuckDuckGo redirect URLs when possible."""
    if "duckduckgo.com/l/" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get("uddg"):
            return urllib.parse.unquote(q["uddg"][0])
    return url

def bing_search(query, max_results=30):
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    try:
        r = S.get(url, timeout=25)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        results = []
        for item in root.findall(".//item"):
            link = item.findtext("link") or ""
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""
            if link:
                results.append({"url": link, "title": title, "snippet": desc, "engine": "bing"})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print("  Bing search error:", e)
        return []

def ddg_search(query, max_results=30):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        r = S.get(url, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for a in soup.select("a.result__a"):
            href = unwrap_search_url(a.get("href") or "")
            if not href:
                continue
            result = a.find_parent(class_="result")
            snippet = ""
            if result:
                sn = result.select_one(".result__snippet")
                snippet = sn.get_text(" ", strip=True) if sn else ""
            results.append({
                "url": href,
                "title": a.get_text(" ", strip=True),
                "snippet": snippet,
                "engine": "duckduckgo"
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print("  DuckDuckGo search error:", e)
        return []

def search_direct_jobs(cfg):
    found = {}
    domains = list(DIRECT_ATS_HOSTS.keys())
    # Search each configured title against each ATS domain.
    for title in cfg["titles"]:
        for domain in domains:
            q = f'site:{domain} "{title}" ("United States" OR Remote OR "Washington DC" OR Virginia OR Maryland)'
            print("Search:", title, "@", domain)
            for row in bing_search(q, cfg.get("results_per_query", 20)) + ddg_search(q, cfg.get("results_per_query", 20)):
                u = canonical_url(unwrap_search_url(row["url"]))
                if is_direct_ats(u):
                    found[u] = row
    return list(found.values())

def first_jsonld_job(soup):
    for tag in soup.find_all("script", attrs={"type":"application/ld+json"}):
        txt = tag.string or tag.get_text()
        if not txt:
            continue
        try:
            obj = json.loads(txt)
        except Exception:
            continue
        objs = obj if isinstance(obj, list) else [obj]
        for x in objs:
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
            if isinstance(x, dict) and isinstance(x.get("@graph"), list):
                for y in x["@graph"]:
                    if isinstance(y, dict) and y.get("@type") == "JobPosting":
                        return y
    return {}

def jsonld_location(j):
    loc = j.get("jobLocation")
    vals = []
    if isinstance(loc, dict):
        loc = [loc]
    if isinstance(loc, list):
        for x in loc:
            if not isinstance(x, dict): continue
            a = x.get("address") or {}
            if isinstance(a, dict):
                s = ", ".join([str(a.get(k) or "").strip() for k in
                               ("addressLocality","addressRegion","addressCountry") if a.get(k)])
                if s: vals.append(s)
    if not vals and str(j.get("jobLocationType","")).upper() == "TELECOMMUTE":
        vals.append("Remote")
    return " · ".join(vals)

def page_metadata(url, search_row):
    try:
        r = S.get(url, timeout=30, allow_redirects=True)
        r.raise_for_status()
        final = canonical_url(r.url)
        if not is_direct_ats(final):
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        j = first_jsonld_job(soup)

        title = clean(j.get("title")) if j else ""
        company = ""
        if j:
            org = j.get("hiringOrganization") or {}
            if isinstance(org, dict):
                company = clean(org.get("name"))
        location = jsonld_location(j) if j else ""
        posted = clean(j.get("datePosted"))[:10] if j and j.get("datePosted") else None
        description = clean(BeautifulSoup(str(j.get("description") or ""), "html.parser").get_text(" ")) if j else ""

        # Fallbacks from page / search result.
        if not title:
            og = soup.find("meta", attrs={"property":"og:title"})
            title = clean(og.get("content")) if og else clean(search_row.get("title"))
        if not company:
            # infer company from ATS path
            parts = [x for x in urllib.parse.urlparse(final).path.split("/") if x]
            company = clean(parts[0].replace("-", " ").replace("_"," ")) if parts else ""
        if not description:
            md = soup.find("meta", attrs={"name":"description"})
            description = clean(md.get("content")) if md else clean(search_row.get("snippet"))
        if not location:
            location = clean(search_row.get("snippet"))

        return {
            "url": final, "title": title, "company": company.title() if company else "Unknown company",
            "location": location, "posted_date": posted, "description": description,
            "source_type": source_type(final),
        }
    except Exception as e:
        print("  Page parse error:", url, e)
        return None

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
    for i in range(0, len(rows), 100):
        r=S.post(f"{SUPABASE_URL}/rest/v1/discovered_jobs",
                 headers=headers, data=json.dumps(rows[i:i+100]), timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase insert failed {r.status_code}: {r.text}")

def main():
    cfg = load_config()
    known = existing_keys()
    search_rows = search_direct_jobs(cfg)
    print(f"Found {len(search_rows)} unique direct ATS URLs from web search.")

    inserts = []
    skipped_title = 0
    skipped_loc = 0

    for sr in search_rows:
        meta = page_metadata(sr["url"], sr)
        if not meta:
            continue
        if not title_matches(meta["title"], cfg):
            skipped_title += 1
            continue
        if not location_matches(meta["location"], cfg):
            skipped_loc += 1
            continue

        k = key_for(meta["url"])
        if k in known:
            continue

        inserts.append({
            "user_id": USER_ID,
            "company": meta["company"],
            "role": meta["title"],
            "job_url": meta["url"],
            "posted_date": meta["posted_date"],
            "location": meta["location"] or None,
            "work_arrangement": "Remote" if "remote" in norm(meta["location"]) else None,
            "source_site": f"{meta['company']} Careers · {meta['source_type'].title()}",
            "description": meta["description"] or None,
            "external_job_id": meta["url"].rstrip("/").split("/")[-1],
            "source_type": meta["source_type"],
            "source_key": k,
            "decision": "new",
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        })
        known.add(k)

    insert(inserts)
    print(f"Inserted {len(inserts)} new direct-company/ATS jobs.")
    print(f"Filtered out {skipped_title} title mismatches and {skipped_loc} non-US/location mismatches.")

if __name__ == "__main__":
    main()
