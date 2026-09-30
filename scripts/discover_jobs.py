#!/usr/bin/env python3
"""
Automatic Job Discovery - V5 Direct ATS Resolver

What this script does:
1. Pulls broad candidate jobs from free public feeds (Jobicy, Remotive, RemoteOK).
2. Filters them to the titles and U.S./remote locations in config/sources.json.
3. Resolves each candidate directly against public ATS endpoints:
   - Ashby
   - Lever
   - Greenhouse
   - SmartRecruiters
4. Stores ONLY direct employer ATS URLs in Supabase.
5. Skips unresolved jobs instead of saving aggregator/third-party links.

No company watchlist is required.
No LinkedIn / Indeed / ZipRecruiter / Appcast links are stored.
"""

import os
import re
import json
import html
import hashlib
import urllib.parse
from datetime import datetime, timezone
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup


# ============================================================
# ENVIRONMENT / CONFIG
# ============================================================

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USER_ID = os.environ["SUPABASE_USER_ID"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
)

REQUEST_TIMEOUT = 18

DIRECT_ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
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

CORPORATE_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "company",
    "co",
    "plc",
    "holdings",
}

MANAGEMENT_BLOCKERS = [
    "director",
    "vice president",
    "vp ",
    "head of",
    "manager, solutions",
    "manager - solutions",
    "manager, sales engineering",
    "manager - sales engineering",
    "engineering manager",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def norm(value):
    value = clean(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


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


def source_type(url):
    return DIRECT_ATS_HOSTS.get(host_of(url), "direct")


def is_direct_ats(url):
    h = host_of(url)

    if any(part in h for part in BLOCKED_HOST_PARTS):
        return False

    return h in DIRECT_ATS_HOSTS


def source_key(url):
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:48]


def first_nonempty(*values):
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def iso_date(value):
    if not value:
        return None

    text = str(value).strip()

    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)

    return None


# ============================================================
# TITLE / LOCATION FILTERING
# ============================================================

def title_matches_config(title, cfg):
    t = norm(title)

    if not t:
        return False

    for excluded in cfg.get("exclude_titles", []):
        if norm(excluded) in t:
            return False

    if any(blocker in t for blocker in MANAGEMENT_BLOCKERS):
        return False

    wanted = [norm(x) for x in cfg.get("titles", [])]

    for target in wanted:
        if not target:
            continue

        if target in t or t in target:
            return True

        target_tokens = set(target.split())
        title_tokens = set(t.split())

        if len(target_tokens) >= 2:
            overlap = len(target_tokens & title_tokens) / len(target_tokens)
            if overlap >= 0.8:
                return True

    return False


def location_matches(location, cfg):
    if not cfg.get("us_only", True):
        return True

    l = norm(location)

    if not l:
        return True

    positives = [
        "united states",
        "usa",
        "u s",
        "remote",
        "north america",
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
        "rockville",
        "baltimore",
    ]

    negatives = [
        "united kingdom",
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


def title_similarity(candidate_title, ats_title):
    a = norm(candidate_title)
    b = norm(ats_title)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if a in b or b in a:
        return 0.93

    a_tokens = set(a.split())
    b_tokens = set(b.split())

    token_score = 0.0
    if a_tokens and b_tokens:
        token_score = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)

    seq_score = SequenceMatcher(None, a, b).ratio()

    return max(token_score, seq_score)


def best_title_match(candidate_title, jobs, get_title, threshold=0.66):
    scored = []

    for job in jobs:
        title = get_title(job)
        score = title_similarity(candidate_title, title)

        if score >= threshold:
            scored.append((score, job))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0]


# ============================================================
# COMPANY SLUG GUESSES
# ============================================================

def strip_corporate_suffixes(company):
    words = norm(company).split()

    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()

    return " ".join(words)


def company_slug_variants(company):
    base = strip_corporate_suffixes(company)

    if not base:
        return []

    words = base.split()
    raw_words = norm(company).split()

    candidates = [
        base,
        "".join(words),
        "-".join(words),
        "_".join(words),
        "".join(raw_words),
        "-".join(raw_words),
    ]

    if len(words) > 1:
        candidates.extend(
            [
                words[0],
                "".join(words[:2]),
                "-".join(words[:2]),
            ]
        )

    aliases = {
        "trm labs": ["trm-labs", "trmlabs"],
        "gitlab": ["gitlab"],
        "datadog": ["datadog"],
        "databricks": ["databricks"],
        "zscaler": ["zscaler"],
        "tenable": ["tenable"],
        "canonical": ["canonical"],
        "ashby": ["ashby", "Ashby"],
        "webflow": ["webflow"],
        "deepgram": ["deepgram"],
        "infisical": ["infisical"],
        "amplitude": ["amplitude"],
        "revenuecat": ["revenuecat"],
        "axiom": ["axiom"],
        "extrahop": ["extrahop"],
        "commvault": ["commvault"],
        "aviatrix": ["aviatrix"],
        "upguard": ["upguard"],
        "semperis": ["semperis"],
    }

    candidates.extend(aliases.get(base, []))

    out = []
    seen = set()

    for item in candidates:
        item = str(item).strip()

        if not item:
            continue

        normalized = re.sub(r"[^A-Za-z0-9_-]", "", item)

        if not normalized:
            continue

        key = normalized.lower()

        if key not in seen:
            seen.add(key)
            out.append(normalized)

    return out[:10]


# ============================================================
# PROVIDER PAGE DIRECT-LINK EXTRACTION
# ============================================================

def direct_from_provider_page(url):
    if not url:
        return None

    try:
        r = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        r.raise_for_status()

        final_url = canonical_url(r.url)

        if is_direct_ats(final_url):
            return final_url

        soup = BeautifulSoup(r.text, "html.parser")

        for tag in [
            soup.find("link", rel="canonical"),
            soup.find("meta", attrs={"property": "og:url"}),
        ]:
            if not tag:
                continue

            candidate = tag.get("href") or tag.get("content")

            if not candidate:
                continue

            candidate = canonical_url(
                urllib.parse.urljoin(final_url, candidate)
            )

            if is_direct_ats(candidate):
                return candidate

        for a in soup.find_all("a", href=True):
            candidate = canonical_url(
                urllib.parse.urljoin(final_url, a.get("href"))
            )

            if is_direct_ats(candidate):
                return candidate

    except Exception:
        pass

    return None


# ============================================================
# ASHBY
# ============================================================

def ashby_resolve(company, candidate_title):
    for board in company_slug_variants(company):
        url = (
            "https://api.ashbyhq.com/posting-api/job-board/"
            + urllib.parse.quote(board, safe="")
        )

        try:
            r = SESSION.get(
                url,
                params={"includeCompensation": "true"},
                timeout=REQUEST_TIMEOUT,
            )

            if r.status_code != 200:
                continue

            data = r.json()
            jobs = data.get("jobs") or []

            match = best_title_match(
                candidate_title,
                jobs,
                lambda j: j.get("title") or "",
            )

            if not match:
                continue

            score, job = match

            job_url = first_nonempty(
                job.get("jobUrl"),
                job.get("applyUrl"),
            )

            if not job_url or not is_direct_ats(job_url):
                continue

            location = first_nonempty(
                job.get("location"),
                "",
            )

            salary_min = None
            salary_max = None

            compensation = job.get("compensation") or {}
            tiers = compensation.get("compensationTiers") or []

            for tier in tiers:
                components = (
                    tier.get("components")
                    or tier.get("summaryComponents")
                    or []
                )

                for component in components:
                    if (
                        str(
                            component.get(
                                "compensationType",
                                "",
                            )
                        ).lower()
                        != "salary"
                    ):
                        continue

                    min_v = component.get("minValue")
                    max_v = component.get("maxValue")

                    if isinstance(min_v, (int, float)):
                        salary_min = int(min_v)

                    if isinstance(max_v, (int, float)):
                        salary_max = int(max_v)

                    if salary_min or salary_max:
                        break

                if salary_min or salary_max:
                    break

            return {
                "url": canonical_url(job_url),
                "title": job.get("title") or candidate_title,
                "location": location,
                "posted_date": None,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "description": clean(
                    job.get("descriptionHtml") or ""
                ),
                "source_type": "ashby",
                "match_score": score,
            }

        except Exception:
            continue

    return None


# ============================================================
# LEVER
# ============================================================

def lever_resolve(company, candidate_title):
    bases = [
        (
            "https://api.lever.co/v0/postings",
            "https://jobs.lever.co",
        ),
        (
            "https://api.eu.lever.co/v0/postings",
            "https://jobs.eu.lever.co",
        ),
    ]

    for site in company_slug_variants(company):
        for api_base, hosted_base in bases:
            try:
                r = SESSION.get(
                    f"{api_base}/{urllib.parse.quote(site, safe='')}",
                    params={
                        "mode": "json",
                        "limit": 200,
                    },
                    headers={
                        "Accept": "application/json",
                    },
                    timeout=REQUEST_TIMEOUT,
                )

                if r.status_code != 200:
                    continue

                jobs = r.json()

                if not isinstance(jobs, list):
                    continue

                match = best_title_match(
                    candidate_title,
                    jobs,
                    lambda j: j.get("text") or "",
                )

                if not match:
                    continue

                score, job = match
                posting_id = str(
                    job.get("id") or ""
                ).strip()

                if not posting_id:
                    continue

                direct_url = (
                    f"{hosted_base}/{site}/{posting_id}"
                )

                categories = (
                    job.get("categories") or {}
                )

                location = first_nonempty(
                    categories.get("location"),
                    ", ".join(
                        categories.get("allLocations")
                        or []
                    ),
                    "",
                )

                description = clean(
                    first_nonempty(
                        job.get("descriptionPlain"),
                        job.get("description"),
                        job.get("additionalPlain"),
                        "",
                    )
                )

                return {
                    "url": canonical_url(direct_url),
                    "title": job.get("text") or candidate_title,
                    "location": location,
                    "posted_date": None,
                    "salary_min": None,
                    "salary_max": None,
                    "description": description,
                    "source_type": "lever",
                    "match_score": score,
                }

            except Exception:
                continue

    return None


# ============================================================
# GREENHOUSE
# ============================================================

def greenhouse_resolve(company, candidate_title):
    for board in company_slug_variants(company):
        url = (
            "https://boards-api.greenhouse.io/v1/boards/"
            + urllib.parse.quote(board, safe="")
            + "/jobs"
        )

        try:
            r = SESSION.get(
                url,
                params={"content": "true"},
                timeout=REQUEST_TIMEOUT,
            )

            if r.status_code != 200:
                continue

            data = r.json()
            jobs = data.get("jobs") or []

            match = best_title_match(
                candidate_title,
                jobs,
                lambda j: j.get("title") or "",
            )

            if not match:
                continue

            score, job = match
            direct_url = job.get("absolute_url") or ""

            if not direct_url:
                continue

            direct_url = canonical_url(direct_url)

            if not is_direct_ats(direct_url):
                job_id = job.get("id")

                if job_id:
                    direct_url = (
                        f"https://boards.greenhouse.io/"
                        f"{board}/jobs/{job_id}"
                    )

            if not is_direct_ats(direct_url):
                continue

            location_obj = (
                job.get("location") or {}
            )

            location = (
                location_obj.get("name") or ""
            )

            posted_date = iso_date(
                first_nonempty(
                    job.get("updated_at"),
                    job.get("created_at"),
                )
            )

            description = clean(
                BeautifulSoup(
                    job.get("content") or "",
                    "html.parser",
                ).get_text(" ")
            )

            return {
                "url": direct_url,
                "title": job.get("title") or candidate_title,
                "location": location,
                "posted_date": posted_date,
                "salary_min": None,
                "salary_max": None,
                "description": description,
                "source_type": "greenhouse",
                "match_score": score,
            }

        except Exception:
            continue

    return None


# ============================================================
# SMARTRECRUITERS
# ============================================================

def smartrecruiters_job_url(
    company_id,
    posting_id,
    title,
):
    title_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        norm(title),
    ).strip("-")

    return canonical_url(
        f"https://jobs.smartrecruiters.com/"
        f"{company_id}/"
        f"{posting_id}-{title_slug}"
    )


def smartrecruiters_resolve(
    company,
    candidate_title,
):
    for company_id in company_slug_variants(company):
        try:
            r = SESSION.get(
                "https://api.smartrecruiters.com/"
                f"v1/companies/"
                f"{urllib.parse.quote(company_id, safe='')}"
                "/postings",
                params={"limit": 100},
                timeout=REQUEST_TIMEOUT,
            )

            if r.status_code != 200:
                continue

            data = r.json()
            jobs = data.get("content") or []

            match = best_title_match(
                candidate_title,
                jobs,
                lambda j: j.get("name") or "",
            )

            if not match:
                continue

            score, job = match
            posting_id = str(
                job.get("id") or ""
            ).strip()

            if not posting_id:
                continue

            title = (
                job.get("name")
                or candidate_title
            )

            direct_url = smartrecruiters_job_url(
                company_id,
                posting_id,
                title,
            )

            location_obj = (
                job.get("location") or {}
            )

            location = clean(
                ", ".join(
                    x
                    for x in [
                        location_obj.get("city"),
                        location_obj.get("region"),
                        location_obj.get("country"),
                    ]
                    if x
                )
            )

            posted_date = iso_date(
                first_nonempty(
                    job.get("releasedDate"),
                    job.get("createdOn"),
                )
            )

            description = ""

            try:
                detail = SESSION.get(
                    "https://api.smartrecruiters.com/"
                    f"v1/companies/"
                    f"{urllib.parse.quote(company_id, safe='')}"
                    f"/postings/"
                    f"{urllib.parse.quote(posting_id, safe='')}",
                    timeout=REQUEST_TIMEOUT,
                )

                if detail.status_code == 200:
                    detail_data = detail.json()

                    sections = (
                        detail_data.get("jobAd")
                        or {}
                    )

                    description_parts = []

                    for section_value in sections.values():
                        if isinstance(
                            section_value,
                            dict,
                        ):
                            text = section_value.get(
                                "text"
                            )

                            if text:
                                description_parts.append(
                                    text
                                )

                    description = clean(
                        BeautifulSoup(
                            " ".join(
                                description_parts
                            ),
                            "html.parser",
                        ).get_text(" ")
                    )

            except Exception:
                pass

            return {
                "url": direct_url,
                "title": title,
                "location": location,
                "posted_date": posted_date,
                "salary_min": None,
                "salary_max": None,
                "description": description,
                "source_type": "smartrecruiters",
                "match_score": score,
            }

        except Exception:
            continue

    return None


# ============================================================
# MASTER ATS RESOLVER
# ============================================================

def resolve_direct_ats(
    company,
    candidate_title,
    provider_url,
):
    direct = direct_from_provider_page(
        provider_url
    )

    if direct:
        return {
            "url": direct,
            "title": candidate_title,
            "location": "",
            "posted_date": None,
            "salary_min": None,
            "salary_max": None,
            "description": "",
            "source_type": source_type(direct),
            "match_score": 1.0,
        }

    resolvers = [
        ("ashby", ashby_resolve),
        ("lever", lever_resolve),
        ("greenhouse", greenhouse_resolve),
        (
            "smartrecruiters",
            smartrecruiters_resolve,
        ),
    ]

    for label, resolver in resolvers:
        result = resolver(
            company,
            candidate_title,
        )

        if result:
            print(
                f"  ATS MATCH [{label}] "
                f"{company} | {candidate_title} "
                f"-> {result['url']}"
            )

            return result

    return None


# ============================================================
# FREE CANDIDATE FEEDS
# ============================================================

def jobicy(cfg):
    seen = set()

    for title in cfg["titles"]:
        try:
            r = SESSION.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={
                    "count": 200,
                    "geo": "usa",
                    "tag": title,
                },
                timeout=25,
            )

            r.raise_for_status()

            for j in r.json().get(
                "jobs",
                [],
            ):
                jid = str(
                    j.get("id") or ""
                )

                if (
                    jid
                    and jid in seen
                ):
                    continue

                if jid:
                    seen.add(jid)

                yield {
                    "provider": "jobicy",
                    "company": clean(
                        j.get("companyName")
                        or ""
                    ),
                    "title": clean(
                        j.get("jobTitle")
                        or ""
                    ),
                    "url": (
                        j.get("url")
                        or ""
                    ),
                    "location": clean(
                        j.get("jobGeo")
                        or "Remote"
                    ),
                    "posted_date": iso_date(
                        j.get("pubDate")
                    ),
                    "description": clean(
                        BeautifulSoup(
                            j.get("jobDescription")
                            or j.get("jobExcerpt")
                            or "",
                            "html.parser",
                        ).get_text(" ")
                    ),
                }

        except Exception as e:
            print(
                "Jobicy error for",
                title,
                ":",
                e,
            )


def remotive():
    try:
        r = SESSION.get(
            "https://remotive.com/api/remote-jobs",
            timeout=25,
        )

        r.raise_for_status()

        for j in r.json().get(
            "jobs",
            [],
        ):
            yield {
                "provider": "remotive",
                "company": clean(
                    j.get("company_name")
                    or ""
                ),
                "title": clean(
                    j.get("title")
                    or ""
                ),
                "url": (
                    j.get("url")
                    or ""
                ),
                "location": clean(
                    j.get(
                        "candidate_required_location"
                    )
                    or "Remote"
                ),
                "posted_date": iso_date(
                    j.get(
                        "publication_date"
                    )
                ),
                "description": clean(
                    BeautifulSoup(
                        j.get("description")
                        or "",
                        "html.parser",
                    ).get_text(" ")
                ),
            }

    except Exception as e:
        print(
            "Remotive error:",
            e,
        )


def remoteok():
    try:
        r = SESSION.get(
            "https://remoteok.com/api",
            timeout=25,
        )

        r.raise_for_status()

        data = r.json()

        if (
            isinstance(data, list)
            and data
            and not data[0].get(
                "position"
            )
        ):
            data = data[1:]

        for j in (
            data
            if isinstance(data, list)
            else []
        ):
            yield {
                "provider": "remoteok",
                "company": clean(
                    j.get("company")
                    or ""
                ),
                "title": clean(
                    j.get("position")
                    or ""
                ),
                "url": (
                    j.get("url")
                    or j.get("apply_url")
                    or ""
                ),
                "location": clean(
                    j.get("location")
                    or "Remote"
                ),
                "posted_date": iso_date(
                    j.get("date")
                ),
                "description": clean(
                    BeautifulSoup(
                        j.get("description")
                        or "",
                        "html.parser",
                    ).get_text(" ")
                ),
            }

    except Exception as e:
        print(
            "RemoteOK error:",
            e,
        )


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_KEY}"
        ),
    }


def existing_keys():
    r = SESSION.get(
        f"{SUPABASE_URL}"
        "/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "source_key",
            "user_id": (
                f"eq.{USER_ID}"
            ),
            "source_key": "not.is.null",
            "limit": "10000",
        },
        timeout=30,
    )

    r.raise_for_status()

    return {
        row["source_key"]
        for row in r.json()
        if row.get("source_key")
    }


def insert_rows(rows):
    if not rows:
        return

    headers = {
        **supabase_headers(),
        "Content-Type": "application/json",
        "Prefer": (
            "return=minimal,"
            "resolution=ignore-duplicates"
        ),
    }

    for i in range(
        0,
        len(rows),
        100,
    ):
        batch = rows[
            i : i + 100
        ]

        r = SESSION.post(
            f"{SUPABASE_URL}"
            "/rest/v1/discovered_jobs",
            headers=headers,
            data=json.dumps(batch),
            timeout=30,
        )

        if (
            r.status_code >= 300
        ):
            raise RuntimeError(
                "Supabase insert failed "
                f"{r.status_code}: "
                f"{r.text}"
            )


# ============================================================
# MAIN
# ============================================================

def main():
    cfg = load_config()
    known_keys = existing_keys()

    provider_rows = []

    provider_rows.extend(
        list(
            jobicy(cfg)
        )
    )

    provider_rows.extend(
        list(
            remotive()
        )
    )

    provider_rows.extend(
        list(
            remoteok()
        )
    )

    candidates = {}

    for job in provider_rows:
        if (
            not job["company"]
            or not job["title"]
        ):
            continue

        if not title_matches_config(
            job["title"],
            cfg,
        ):
            continue

        if not location_matches(
            job["location"],
            cfg,
        ):
            continue

        key = (
            norm(job["company"]),
            norm(job["title"]),
        )

        current = candidates.get(
            key
        )

        if current is None:
            candidates[key] = job

        else:
            current_score = (
                len(
                    current.get(
                        "description"
                    )
                    or ""
                )
                +
                (
                    100
                    if current.get(
                        "url"
                    )
                    else 0
                )
            )

            new_score = (
                len(
                    job.get(
                        "description"
                    )
                    or ""
                )
                +
                (
                    100
                    if job.get(
                        "url"
                    )
                    else 0
                )
            )

            if (
                new_score >
                current_score
            ):
                candidates[key] = job

    print(
        "Candidate jobs after "
        "title/location filtering: "
        f"{len(candidates)}"
    )

    rows = []
    resolved = 0
    unresolved = 0
    duplicates = 0

    for candidate in candidates.values():
        company = (
            candidate["company"]
        )

        title = (
            candidate["title"]
        )

        print(
            f"Resolving: "
            f"{company} | {title}"
        )

        ats = resolve_direct_ats(
            company,
            title,
            candidate.get("url")
            or "",
        )

        if not ats:
            unresolved += 1

            print(
                f"UNRESOLVED: "
                f"{company} | {title}"
            )

            continue

        direct_url = canonical_url(
            ats["url"]
        )

        if not is_direct_ats(
            direct_url
        ):
            unresolved += 1

            print(
                "SKIPPED NON-DIRECT URL: "
                f"{company} | {title} "
                f"-> {direct_url}"
            )

            continue

        key = source_key(
            direct_url
        )

        if key in known_keys:
            duplicates += 1

            print(
                f"ALREADY KNOWN: "
                f"{company} | {title}"
            )

            continue

        resolved_title = (
            ats.get("title")
            or title
        )

        resolved_location = first_nonempty(
            ats.get("location"),
            candidate.get("location"),
            "",
        )

        resolved_posted_date = first_nonempty(
            ats.get("posted_date"),
            candidate.get("posted_date"),
        )

        resolved_description = first_nonempty(
            ats.get("description"),
            candidate.get("description"),
            "",
        )

        row = {
            "user_id": USER_ID,

            "company": company,

            "role": resolved_title,

            "job_url": direct_url,

            "posted_date": (
                resolved_posted_date
            ),

            "location": (
                resolved_location
                or None
            ),

            "work_arrangement": (
                "Remote"
                if "remote"
                in norm(
                    resolved_location
                )
                else None
            ),

            "salary_min": (
                ats.get(
                    "salary_min"
                )
            ),

            "salary_max": (
                ats.get(
                    "salary_max"
                )
            ),

            "source_site": (
                f"{company} Careers · "
                f"{ats.get(
                    'source_type',
                    source_type(
                        direct_url
                    )
                ).title()}"
            ),

            "description": (
                resolved_description
                or None
            ),

            "external_job_id": (
                direct_url
                .rstrip("/")
                .split("/")[-1]
            ),

            "source_type": (
                ats.get(
                    "source_type",
                    source_type(
                        direct_url
                    ),
                )
            ),

            "source_key": key,

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
        }

        rows.append(row)
        known_keys.add(key)
        resolved += 1

        print(
            f"RESOLVED: "
            f"{company} | "
            f"{resolved_title} "
            f"-> {direct_url}"
        )

    insert_rows(rows)

    print("")
    print(
        "========== "
        "JOB DISCOVERY SUMMARY "
        "=========="
    )

    print(
        f"Candidates checked: "
        f"{len(candidates)}"
    )

    print(
        "Resolved direct ATS postings: "
        f"{resolved}"
    )

    print(
        "Already known / duplicates: "
        f"{duplicates}"
    )

    print(
        "Unresolved candidates skipped: "
        f"{unresolved}"
    )

    print(
        "Inserted new "
        "direct-company/ATS jobs: "
        f"{len(rows)}"
    )

    print(
        "==========================================="
    )


if __name__ == "__main__":
    main()
