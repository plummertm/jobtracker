#!/usr/bin/env python3
"""
V4 automatic job discovery.

Goal:
- NO company list
- NO LinkedIn/Indeed/ZipRecruiter/Appcast URLs stored
- ONLY direct ATS/company-career apply URLs stored
- FREE sources only

Discovery strategy:
1) Pull broad title-matching jobs from free public feeds:
   - Jobicy
   - Remotive
   - RemoteOK
2) For each candidate, try to resolve the employer's direct ATS/careers URL by:
   - extracting canonical/employer links from the posting page
   - searching Bing RSS for the exact company + title restricted to direct ATS hosts
3) Only insert if a direct ATS URL is found.

This avoids DuckDuckGo, which was blocking GitHub Actions.
"""

import os
import re
import json
import html
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from bs4 import BeautifulSoup
import requests


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USER_ID = os.environ["SUPABASE_USER_ID"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")

S = requests.Session()
S.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
})

DIRECT_ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "jobs.smartrecruiters.com": "smartrecruiters",
}

BLOCKED_HOST_PARTS = [
    "linkedin.",
    "indeed.",
    "ziprecruiter.",
    "appcast.",
    "glassdoor.",
    "monster.",
    "careerbuilder.",
    "simplyhired.",
    "talent.com",
    "jooble.",
    "remoteok.",
    "remotive.",
    "jobicy.",
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
        return urllib.parse.urlunsplit(
            (
                p.scheme or "https",
                p.netloc.lower(),
                p.path.rstrip("/"),
                "",
                "",
            )
        )
    except Exception:
        return url


def is_direct_ats(url):
    h = host_of(url)

    if any(x in h for x in BLOCKED_HOST_PARTS):
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

    positives = [
        "united states",
        "usa",
        "u.s.",
        "remote",
        "us remote",
        "north america",
        "washington, dc",
        "washington dc",
        "district of columbia",
        "maryland",
        "virginia",
        "arlington",
        "mclean",
        "reston",
        "herndon",
        "alexandria",
        "tysons",
        "fairfax",
        "bethesda",
    ]

    negatives = [
        "united kingdom",
        " uk",
        "canada",
        "toronto",
        "vancouver",
        "europe",
        "emea",
        "india",
        "germany",
        "france",
        "spain",
        "italy",
        "australia",
        "singapore",
        "japan",
        "brazil",
        "mexico",
        "poland",
        "romania",
        "netherlands",
        "ireland",
    ]

    if any(x in l for x in positives):
        return True

    if any(x in l for x in negatives):
        return False

    return True


def key_for(url):
    return hashlib.sha256(
        canonical_url(url).encode("utf-8")
    ).hexdigest()[:48]


def bing_search(query, max_results=10):
    url = (
        "https://www.bing.com/search?format=rss&q="
        + urllib.parse.quote(query)
    )

    try:
        r = S.get(url, timeout=20)
        r.raise_for_status()

        root = ET.fromstring(r.text)

        out = []

        for item in root.findall(".//item"):
            link = item.findtext("link") or ""
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""

            if link:
                out.append({
                    "url": link,
                    "title": title,
                    "snippet": desc,
                })

            if len(out) >= max_results:
                break

        return out

    except Exception as e:
        print("Bing error:", e)
        return []


def direct_from_page(url):
    try:
        r = S.get(
            url,
            timeout=20,
            allow_redirects=True,
        )

        r.raise_for_status()

        final = canonical_url(r.url)

        if is_direct_ats(final):
            return final

        soup = BeautifulSoup(r.text, "html.parser")

        possible_canonical = [
            soup.find("link", rel="canonical"),
            soup.find("meta", attrs={"property": "og:url"}),
        ]

        for tag in possible_canonical:
            if not tag:
                continue

            candidate = tag.get("href") or tag.get("content")

            if candidate:
                candidate = canonical_url(
                    urllib.parse.urljoin(final, candidate)
                )

                if is_direct_ats(candidate):
                    return candidate

        for a in soup.find_all("a", href=True):
            u = canonical_url(
                urllib.parse.urljoin(final, a["href"])
            )

            if is_direct_ats(u):
                return u

    except Exception:
        pass

    return None


def resolve_direct_ats(company, title, provider_url):
    direct = direct_from_page(provider_url)

    if direct:
        return direct

    for domain in DIRECT_ATS_HOSTS:
        q = f'site:{domain} "{company}" "{title}"'

        for row in bing_search(q, 8):
            u = canonical_url(row["url"])

            if not is_direct_ats(u):
                continue

            blob = norm(
                row["title"]
                + " "
                + row["snippet"]
            )

            company_tokens = [
                x
                for x in re.split(r"\W+", norm(company))
                if len(x) >= 3
            ]

            title_tokens = [
                x
                for x in re.split(r"\W+", norm(title))
                if len(x) >= 4
            ]

            comp_ok = (
                not company_tokens
                or any(
                    x in blob
                    for x in company_tokens[:3]
                )
            )

            title_ok = (
                not title_tokens
                or any(
                    x in blob
                    for x in title_tokens[:4]
                )
            )

            if comp_ok and title_ok:
                return u

    return None


def jobicy(cfg):
    seen = set()

    for title in cfg["titles"]:
        try:
            r = S.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={
                    "count": 200,
                    "geo": "usa",
                    "tag": title,
                },
                timeout=25,
            )

            r.raise_for_status()

            for j in r.json().get("jobs", []):
                jid = str(j.get("id") or "")

                if jid in seen:
                    continue

                seen.add(jid)

                yield {
                    "provider": "jobicy",
                    "company": j.get("companyName") or "",
                    "title": j.get("jobTitle") or "",
                    "url": j.get("url") or "",
                    "location": j.get("jobGeo") or "Remote",
                    "posted_date": (
                        str(j.get("pubDate") or "")[:10]
                        or None
                    ),
                    "description": clean(
                        j.get("jobDescription")
                        or j.get("jobExcerpt")
                        or ""
                    ),
                }

        except Exception as e:
            print(
                "Jobicy error for",
                title,
                ":",
                e,
            )


def remotive(cfg):
    try:
        r = S.get(
            "https://remotive.com/api/remote-jobs",
            timeout=25,
        )

        r.raise_for_status()

        for j in r.json().get("jobs", []):
            yield {
                "provider": "remotive",
                "company": j.get("company_name") or "",
                "title": j.get("title") or "",
                "url": j.get("url") or "",
                "location": (
                    j.get("candidate_required_location")
                    or "Remote"
                ),
                "posted_date": (
                    str(j.get("publication_date") or "")[:10]
                    or None
                ),
                "description": clean(
                    j.get("description") or ""
                ),
            }

    except Exception as e:
        print("Remotive error:", e)


def remoteok(cfg):
    try:
        r = S.get(
            "https://remoteok.com/api",
            timeout=25,
        )

        r.raise_for_status()

        data = r.json()

        if (
            isinstance(data, list)
            and data
            and not data[0].get("position")
        ):
            data = data[1:]

        for j in data if isinstance(data, list) else []:
            yield {
                "provider": "remoteok",
                "company": j.get("company") or "",
                "title": j.get("position") or "",
                "url": (
                    j.get("url")
                    or j.get("apply_url")
                    or ""
                ),
                "location": (
                    j.get("location")
                    or "Remote"
                ),
                "posted_date": (
                    str(j.get("date") or "")[:10]
                    or None
                ),
                "description": clean(
                    j.get("description") or ""
                ),
            }

    except Exception as e:
        print("RemoteOK error:", e)


def existing_keys():
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_KEY}"
        ),
    }

    params = {
        "select": "source_key",
        "user_id": f"eq.{USER_ID}",
        "source_key": "not.is.null",
        "limit": "10000",
    }

    r = S.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=headers,
        params=params,
        timeout=30,
    )

    r.raise_for_status()

    return {
        x["source_key"]
        for x in r.json()
        if x.get("source_key")
    }


def insert(rows):
    if not rows:
        return

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_KEY}"
        ),
        "Content-Type": "application/json",
        "Prefer": (
            "return=minimal,"
            "resolution=ignore-duplicates"
        ),
    }

    for i in range(0, len(rows), 100):
        r = S.post(
            f"{SUPABASE_URL}/rest/v1/discovered_jobs",
            headers=headers,
            data=json.dumps(
                rows[i:i + 100]
            ),
            timeout=30,
        )

        if r.status_code >= 300:
            raise RuntimeError(
                "Supabase insert failed "
                f"{r.status_code}: {r.text}"
            )


def main():
    cfg = load_config()
    known = existing_keys()

    providers = []

    providers.extend(
        list(jobicy(cfg))
    )

    providers.extend(
        list(remotive(cfg))
    )

    providers.extend(
        list(remoteok(cfg))
    )

    candidates = {}

    for j in providers:
        if not title_matches(
            j["title"],
            cfg,
        ):
            continue

        if not location_matches(
            j["location"],
            cfg,
        ):
            continue

        k = (
            norm(j["company"]),
            norm(j["title"]),
        )

        candidates[k] = j

    print(
        "Candidate jobs after "
        "title/location filtering:",
        len(candidates),
    )

    rows = []
    unresolved = 0
    resolved = 0

    for j in candidates.values():
        direct = resolve_direct_ats(
            j["company"],
            j["title"],
            j["url"],
        )

        if not direct:
            unresolved += 1

            print(
                "UNRESOLVED:",
                j["company"],
                "|",
                j["title"],
            )

            continue

        k = key_for(direct)

        if k in known:
            continue

        rows.append({
            "user_id": USER_ID,
            "company": (
                j["company"]
                or "Unknown company"
            ),
            "role": j["title"],
            "job_url": direct,
            "posted_date": (
                j["posted_date"]
            ),
            "location": (
                j["location"]
                or None
            ),
            "work_arrangement": (
                "Remote"
                if "remote" in norm(
                    j["location"]
                )
                else None
            ),
            "source_site": (
                f"{j['company']} Careers · "
                f"{source_type(direct).title()}"
            ),
            "description": (
                j["description"]
                or None
            ),
            "external_job_id": (
                direct
                .rstrip("/")
                .split("/")[-1]
            ),
            "source_type": (
                source_type(direct)
            ),
            "source_key": k,
            "decision": "new",
            "first_seen_at": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            "last_seen_at": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
        })

        known.add(k)
        resolved += 1

        print(
            "RESOLVED:",
            j["company"],
            "|",
            j["title"],
            "->",
            direct,
        )

    insert(rows)

    print(
        f"Resolved {resolved} "
        "direct ATS postings."
    )

    print(
        f"Unresolved {unresolved} "
        "candidates were skipped."
    )

    print(
        f"Inserted {len(rows)} new "
        "direct-company/ATS jobs."
    )


if __name__ == "__main__":
    main()
