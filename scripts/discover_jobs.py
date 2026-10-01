#!/usr/bin/env python3

import os
import re
import json
import html
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USER_ID = os.environ["SUPABASE_USER_ID"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
})

REQUEST_TIMEOUT = 18

DIRECT_ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "jobs.smartrecruiters.com": "smartrecruiters",
}

GENERIC_JOB_PATH_MARKERS = (
    "/careers/job/",
    "/careers/jobs/",
    "/careers/position/",
    "/careers/open-positions/",
    "/career/job/",
    "/career/jobs/",
    "/company/careers/",
    "/jobs/job/",
    "/jobs/",
    "/job/",
    "/openings/",
    "/positions/",
    "/opportunities/",
)

JOB_ID_QUERY_KEYS = {
    "gh_jid", "jobid", "job_id", "jid", "reqid", "req_id",
    "requisitionid", "requisition_id", "requisition", "job"
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
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "holdings"
}

FOREIGN_LOCATION_TERMS = [
    "canada", "toronto", "vancouver", "montreal", "ottawa",
    "australia", "sydney", "melbourne", "brisbane",
    "costa rica",
    "united kingdom", "london", "england", "scotland",
    "ireland", "dublin",
    "europe", "emea",
    "germany", "france", "spain", "italy", "netherlands",
    "poland", "romania", "india", "singapore", "japan",
    "brazil", "mexico"
]

EAST_COAST_TERMS = [
    "east coast", "eastern us", "eastern united states", "eastern time",
    "northeast", "mid atlantic", "mid-atlantic", "southeast",
    "washington dc", "district of columbia", "dc metro", "dmv",
    "maryland", "virginia", "arlington", "alexandria", "mclean", "tysons",
    "reston", "herndon", "fairfax", "bethesda", "rockville", "baltimore",
    "new york", "new jersey", "pennsylvania", "philadelphia", "delaware",
    "connecticut", "massachusetts", "boston", "rhode island", "maine",
    "new hampshire", "vermont", "north carolina", "south carolina",
    "georgia", "atlanta", "florida", "miami", "tampa", "orlando"
]

US_ELIGIBLE_TERMS = [
    "united states", "usa", "u s", "us",
    "remote us", "remote usa", "remote united states", "united states remote",
    "us remote", "u s remote", "anywhere in the us", "anywhere in the united states",
    "north america", "americas"
]

GLOBAL_REMOTE_TERMS = [
    "anywhere", "worldwide", "global remote", "remote worldwide"
]

COMPANY_NAME_ALIASES = {
    # Rebrands / names used by provider feeds versus current careers sites.
    "tripactions": ["Navan"],
    "navan": ["TripActions"],
    "built technologies": ["Built", "GetBuilt"],
    "forma ai": ["Forma.ai", "Forma AI"],
}

# Resume-supported evidence used for fit scoring.
# This is intentionally limited to experience explicitly supported by Toni's resume.
RESUME_PROFILE = {
    "core_presales": {
        "weight": 30,
        "skills": {
            "technical discovery": ["technical discovery", "discovery"],
            "demos": ["demo", "demonstration"],
            "workshops": ["workshop"],
            "pocs/povs": ["poc", "proof of concept", "pov", "proof of value", "evaluation"],
            "architecture reviews": ["architecture review", "solution architecture", "solutions architecture"],
            "executive presentations": ["executive presentation", "executive-facing", "executive audience"],
            "rfp/rfi": ["rfp", "rfi"],
            "sales partnership": ["account executive", "ae partnership", "sales team", "presales", "pre-sales", "sales engineering"],
            "voice of customer": ["voice of customer", "customer feedback", "field feedback"],
        },
    },
    "cloud_platform": {
        "weight": 25,
        "skills": {
            "AWS": ["aws", "amazon web services"],
            "Azure": ["azure"],
            "GCP": ["gcp", "google cloud"],
            "Kubernetes": ["kubernetes", "eks"],
            "Docker": ["docker", "container"],
            "networking": ["networking", "vpc", "subnet", "routing"],
            "IAM": ["iam", "rbac", "identity and access"],
            "high availability": ["high availability", "resiliency", "disaster recovery"],
            "CloudFormation": ["cloudformation"],
            "REST APIs": ["rest api", "restful api", "api integration", "apis"],
        },
    },
    "security_federal": {
        "weight": 15,
        "skills": {
            "FedRAMP": ["fedramp"],
            "FISMA": ["fisma"],
            "NIST 800-53": ["nist 800-53", "nist 800 53"],
            "RMF/ATO": ["rmf", "ato", "authorization to operate"],
            "Zero Trust": ["zero trust"],
            "cloud security": ["cloud security"],
            "federal/public sector": ["federal", "public sector", "government", "civilian agency"],
            "risk/vulnerability": ["vulnerability", "risk remediation", "security controls"],
        },
    },
    "ai_data_automation": {
        "weight": 15,
        "skills": {
            "Generative AI": ["generative ai", "genai", "gen ai"],
            "LLMs": ["llm", "large language model"],
            "RAG": ["rag", "retrieval augmented generation"],
            "AI/ML": ["machine learning", "ai/ml", "artificial intelligence"],
            "Python": ["python"],
            "SQL": ["sql"],
            "PostgreSQL": ["postgresql", "postgres"],
            "MySQL": ["mysql"],
            "GitHub/GitLab": ["github", "gitlab"],
            "Ansible": ["ansible"],
            "CI/CD": ["ci/cd", "cicd", "continuous integration"],
        },
    },
    "customer_strategy": {
        "weight": 15,
        "skills": {
            "customer-facing": ["customer-facing", "customer facing", "customers"],
            "stakeholder communication": ["stakeholder", "cross-functional", "cross functional"],
            "product/engineering collaboration": ["product team", "engineering team", "product and engineering", "product/engineering"],
            "technical advising": ["technical advisor", "trusted advisor", "technical guidance"],
            "troubleshooting": ["troubleshooting", "debugging", "production issue"],
        },
    },
}

# Common requirements that are NOT supported directly by the resume and should be surfaced as gaps.
GAP_SKILLS = {
    "Terraform": ["terraform"],
    "Kafka/Confluent": ["kafka", "confluent"],
    "Helm": ["helm"],
    "Go": ["golang", "go language"],
    "Ruby": ["ruby"],
    "Snowflake": ["snowflake"],
    "Splunk": ["splunk"],
    "Service mesh": ["istio", "service mesh"],
}

RESUME_RELEVANT_YEARS = 6

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def clean(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()

def norm(value):
    value = clean(value).lower().replace("&", " and ")
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
        # Preserve query parameters that identify a specific job. This matters
        # for branded Greenhouse/company career pages such as ?gh_jid=12345.
        kept = []
        for key, value in urllib.parse.parse_qsl(p.query, keep_blank_values=False):
            if key.lower() in JOB_ID_QUERY_KEYS and value:
                kept.append((key, value))
        query = urllib.parse.urlencode(kept)
        return urllib.parse.urlunsplit(
            (p.scheme or "https", p.netloc.lower(), p.path.rstrip("/"), query, "")
        )
    except Exception:
        return url

def classify_direct_job_url(url):
    host = host_of(url)
    path = urllib.parse.urlsplit(url).path.lower() if url else ""

    if not host or any(part in host for part in BLOCKED_HOST_PARTS):
        return None

    if host in DIRECT_ATS_HOSTS:
        return DIRECT_ATS_HOSTS[host]

    # Many companies use a branded careers domain in front of Greenhouse.
    # The gh_jid query parameter identifies the specific Greenhouse job.
    query_keys = {k.lower() for k, _ in urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)}
    if "gh_jid" in query_keys:
        return "greenhouse"

    if host.endswith(".myworkdayjobs.com"):
        return "workday"

    if host.endswith(".icims.com"):
        return "icims"

    if host.endswith(".oraclecloud.com") and "/hcmui/candidateexperience/" in path:
        return "oracle"

    if host.endswith(".taleo.net"):
        return "taleo"

    if host == "app.eightfold.ai" or host.endswith(".eightfold.ai"):
        if "/careers" in path or "/job" in path:
            return "eightfold"

    # Generic direct company-career pages are allowed only when the URL itself
    # looks like a specific job/careers route. Aggregators are rejected above.
    if any(marker in path for marker in GENERIC_JOB_PATH_MARKERS):
        return "company-careers"

    return None

def source_type(url):
    return classify_direct_job_url(url) or "direct"

def is_direct_ats(url):
    # Historical function name retained because the rest of the collector uses it.
    # It now means a direct ATS OR a direct company-careers job page.
    return classify_direct_job_url(url) is not None

def source_key(url):
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:48]

def iso_date(value):
    if not value:
        return None

    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value))
    return match.group(0) if match else None

def parse_iso_date(value):
    value = iso_date(value)

    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None

def is_too_old(posted_date, max_days):
    parsed = parse_iso_date(posted_date)

    if not parsed:
        return False

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max_days)
    return parsed < cutoff

def company_is_excluded(company, cfg):
    normalized = norm(company)

    for excluded in cfg.get("exclude_companies", []):
        target = norm(excluded)

        if target and (normalized == target or target in normalized):
            return True

    return False

def title_matches_config(title, cfg):
    title_n = norm(title)

    if not title_n:
        return False

    for excluded in cfg.get("exclude_titles", []):
        if norm(excluded) in title_n:
            return False

    for wanted in cfg.get("titles", []):
        target = norm(wanted)

        if target and (target in title_n or title_n in target):
            return True

    return False

def _has_location_phrase(location_n, phrase):
    phrase_n = norm(phrase)
    if not phrase_n:
        return False
    return f" {phrase_n} " in f" {location_n} "


def location_matches(location, cfg):
    if not cfg.get("us_only", True):
        return True

    location_n = norm(location)

    if not location_n:
        return False

    # If the posting explicitly includes the U.S., keep it even when other
    # countries/regions are also listed. Examples: "Canada, USA" or
    # "Canada, Europe, USA". Toni can still apply to the U.S. version.
    if any(_has_location_phrase(location_n, term) for term in US_ELIGIBLE_TERMS):
        return True

    # "Anywhere" / worldwide remote roles are allowed to continue to ATS
    # resolution because they include U.S. applicants unless the posting later
    # resolves to a specifically foreign-only location.
    if any(_has_location_phrase(location_n, term) for term in GLOBAL_REMOTE_TERMS):
        return True

    # Explicit foreign-only location with no U.S. eligibility remains excluded.
    if any(_has_location_phrase(location_n, term) for term in FOREIGN_LOCATION_TERMS):
        return False

    # Plain Remote is allowed; final ATS resolution is checked again below.
    if location_n == "remote" or location_n.startswith("remote "):
        return True

    # East Coast / DMV.
    if any(_has_location_phrase(location_n, term) for term in EAST_COAST_TERMS):
        return True

    return False

def requires_active_ts(*parts):
    text = " ".join(clean(part) for part in parts if part).lower()
    if not text:
        return False

    # Evaluate sentence-sized fragments so "ability to obtain" does not mask an
    # unrelated active-clearance requirement elsewhere in the posting.
    fragments = re.split(r"[\n\r.!?;]+", text)
    clearance_terms = ("top secret", "ts/sci", "ts sci", "tssci")
    obtain_terms = (
        "ability to obtain", "able to obtain", "eligible to obtain",
        "can obtain", "willing to obtain", "obtain a top secret",
        "obtain top secret", "sponsorship for", "sponsor for"
    )
    active_terms = (
        "active", "current", "currently hold", "must possess", "must hold",
        "required", "requirement", "requires", "possess a", "hold a"
    )

    for fragment in fragments:
        if not any(term in fragment for term in clearance_terms):
            continue
        if any(term in fragment for term in obtain_terms) and not any(
            term in fragment for term in ("active", "current", "currently hold", "must possess", "must hold")
        ):
            continue
        if any(term in fragment for term in active_terms):
            return True
        # A bare "TS/SCI clearance" or "Top Secret clearance" in required-qualification
        # text is treated as an active-clearance requirement unless the posting says it can be obtained.
        if "clearance" in fragment and not any(term in fragment for term in obtain_terms):
            return True

    return False


def _contains_any(text, phrases):
    return any(phrase in text for phrase in phrases)


def calculate_match(title, description, location=""):
    text = norm(" ".join([title or "", description or "", location or ""]))
    raw_text = clean(" ".join([title or "", description or "", location or ""])).lower()

    # Title alignment is real evidence but deliberately capped so a title alone
    # cannot create an inflated match percentage.
    title_n = norm(title)
    title_score = 16
    if any(term in title_n for term in [
        "solutions engineer", "sales engineer", "solutions architect",
        "customer engineer", "forward deployed engineer", "presales engineer",
        "pre sales engineer"
    ]):
        title_score = 20

    matched_labels = []
    missing_labels = []
    earned = 0.0
    available = 0.0

    for category in RESUME_PROFILE.values():
        weight = float(category["weight"])
        detected = []
        matched = []
        for label, phrases in category["skills"].items():
            phrases_n = [norm(p) for p in phrases]
            if _contains_any(text, phrases_n):
                detected.append(label)
                matched.append(label)  # every item in this profile is resume-supported

        if detected:
            available += weight
            ratio = len(matched) / len(detected)
            earned += weight * ratio
            matched_labels.extend(matched)

    # If the posting does not mention many recognizable technologies, do not
    # punish it for missing keywords; use the title/customer-facing evidence only.
    if available:
        requirement_score = (earned / available) * 70.0
    else:
        requirement_score = 48.0

    score = title_score + requirement_score

    # Experience-level penalty based strictly on the resume timeline.
    years = [int(x) for x in re.findall(r"(?:minimum of |at least |minimum )?(\d{1,2})\+?\s*(?:years|yrs)", raw_text)]
    required_years = max(years) if years else 0
    if required_years > RESUME_RELEVANT_YEARS:
        score -= min(20, (required_years - RESUME_RELEVANT_YEARS) * 4)
        missing_labels.append(f"{required_years}+ years requested")

    for label, phrases in GAP_SKILLS.items():
        if _contains_any(text, [norm(p) for p in phrases]):
            missing_labels.append(label)
            score -= 3

    score = max(35, min(96, round(score)))

    # Keep explanations compact and grounded in resume evidence.
    strengths = []
    preferred_order = [
        "technical discovery", "demos", "pocs/povs", "AWS", "Azure", "GCP",
        "Kubernetes", "federal/public sector", "FedRAMP", "NIST 800-53",
        "Python", "Generative AI", "LLMs", "RAG", "customer-facing",
        "stakeholder communication", "troubleshooting"
    ]
    matched_set = set(matched_labels)
    for label in preferred_order:
        if label in matched_set and label not in strengths:
            strengths.append(label)
        if len(strengths) == 4:
            break

    if not strengths:
        strengths = ["solutions engineering / architecture title alignment"]

    # Deduplicate gaps while preserving order.
    gaps = []
    for label in missing_labels:
        if label not in gaps:
            gaps.append(label)

    strength_text = ", ".join(strengths)
    if gaps:
        gap_text = ", ".join(gaps[:3])
        summary = f"Strong: {strength_text}. Gaps from resume evidence: {gap_text}. No active-TS blocker identified."
    else:
        summary = f"Strong: {strength_text}. No major resume-evidence gap detected in the parsed posting. No active-TS blocker identified."

    return score, summary



def _money_to_number(raw):
    if raw is None:
        return None
    text = str(raw).strip().lower().replace(',', '')
    mult = 1
    if text.endswith('k'):
        mult = 1000
        text = text[:-1]
    try:
        value = float(text) * mult
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(round(value))


def extract_base_compensation(description):
    """Return (salary_min, salary_max) for a clearly annual/base salary range.

    We intentionally require compensation-language near the numbers so revenue, ARR,
    contract values, bonus targets, etc. are not mistaken for salary.
    """
    text = clean(description or '')
    if not text:
        return None, None

    # Normalize dash variants but keep original words for context checks.
    normalized = text.replace('–', '-').replace('—', '-')

    context_terms = (
        'base compensation', 'base salary', 'salary range', 'compensation range',
        'annual salary', 'annual base', 'pay range', 'base pay', 'salary'
    )
    reject_terms = ('ote', 'on-target earnings', 'on target earnings', 'bonus', 'commission')

    money = r'\$?\s*(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s*[kK])'
    range_re = re.compile(
        money + r'\s*(?:-|to|through)\s*\$?\s*(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s*[kK])',
        re.I,
    )

    candidates = []
    for m in range_re.finditer(normalized):
        start = max(0, m.start() - 180)
        end = min(len(normalized), m.end() + 180)
        ctx = normalized[start:end].lower()
        before = normalized[max(0, m.start() - 120):m.start()].lower()
        if not any(term in ctx for term in context_terms):
            continue

        lo = _money_to_number(m.group(1))
        hi = _money_to_number(m.group(2))
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo

        # Ignore hourly-looking or obviously non-salary ranges.
        if lo < 20000 or hi < 20000:
            continue

        # Prefer base/salary context over OTE/bonus context.
        base_terms = (
            'base compensation', 'base salary', 'annual base', 'base pay', 'salary range', 'annual salary'
        )
        # The label must precede this specific numeric range. This prevents an
        # earlier OTE range from borrowing a later "base salary" label.
        base_signal = any(term in before for term in base_terms)
        rejected = any(term in before for term in reject_terms) and not base_signal
        if rejected:
            continue

        candidates.append((0 if base_signal else 1, m.start(), lo, hi))

    if not candidates:
        return None, None

    candidates.sort()
    _, _, lo, hi = candidates[0]
    return lo, hi

def title_similarity(candidate, actual):
    a = norm(candidate)
    b = norm(actual)

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

def best_match(candidate_title, postings, title_getter, threshold=0.66):
    matches = []

    for posting in postings:
        score = title_similarity(candidate_title, title_getter(posting))

        if score >= threshold:
            matches.append((score, posting))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)

    return matches[0]

def company_slug_variants(company):
    words = norm(company).split()

    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()

    if not words:
        return []

    base = " ".join(words)

    candidates = [
        base,
        "".join(words),
        "-".join(words),
        "_".join(words),
    ]

    aliases = {
        "trm labs": ["trm-labs", "trmlabs"],
        "gitlab": ["gitlab"],
        "databricks": ["databricks"],
        "zscaler": ["zscaler"],
        "tenable": ["tenable"],
        "canonical": ["canonical"],
        "ashby": ["ashby"],
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

        # Verified branded ATS board aliases. These are resolver aliases only;
        # they are not a company watchlist and do not control which companies
        # are discovered.
        "forma ai": ["formaaiinc", "formaai"],
        "echodyne": ["echodynecorp", "echodyne"],
        "built technologies": ["getbuilt", "builttechnologies"],
        "nebius": ["nebius"],
        "tripactions": ["navan", "tripactions"],
        "navan": ["navan", "tripactions"],
    }

    candidates.extend(aliases.get(base, []))

    out = []
    seen = set()

    for candidate in candidates:
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "", candidate)

        if not cleaned:
            continue

        key = cleaned.lower()

        if key not in seen:
            seen.add(key)
            out.append(cleaned)

    return out[:10]

def _jsonld_jobposting(soup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        items = payload if isinstance(payload, list) else [payload]
        expanded = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                expanded.extend(item["@graph"])
            else:
                expanded.append(item)

        for item in expanded:
            if not isinstance(item, dict):
                continue
            typ = item.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "JobPosting" in types:
                return item
    return None


def _schema_location(jobposting):
    if not isinstance(jobposting, dict):
        return ""

    if str(jobposting.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        req = jobposting.get("applicantLocationRequirements")
        reqs = req if isinstance(req, list) else [req]
        places = []
        for item in reqs:
            if not isinstance(item, dict):
                continue
            name = clean(item.get("name") or "")
            if name:
                places.append(name)
        return "Remote" + (" - " + ", ".join(places) if places else "")

    locations = jobposting.get("jobLocation")
    locations = locations if isinstance(locations, list) else [locations]
    out = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address") or {}
        if not isinstance(address, dict):
            address = {}
        bits = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        text = ", ".join(clean(x) for x in bits if clean(x))
        if text:
            out.append(text)
    return "; ".join(dict.fromkeys(out))


def fetch_direct_job_details(url, fallback_title=""):
    try:
        r = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None

        final_url = canonical_url(r.url)
        direct_type = classify_direct_job_url(final_url)
        if not direct_type:
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        jp = _jsonld_jobposting(soup)

        title = fallback_title
        description = ""
        posted_date = None
        location = ""

        if jp:
            title = clean(jp.get("title") or fallback_title)
            description = clean(
                BeautifulSoup(str(jp.get("description") or ""), "html.parser").get_text(" ")
            )
            posted_date = iso_date(jp.get("datePosted"))
            location = _schema_location(jp)

        if not title:
            meta = soup.find("meta", attrs={"property": "og:title"})
            title = clean(meta.get("content") if meta else "")

        if not description:
            meta = (
                soup.find("meta", attrs={"name": "description"})
                or soup.find("meta", attrs={"property": "og:description"})
            )
            description = clean(meta.get("content") if meta else "")

        # Some ATS pages render the full JD server-side without JSON-LD.
        if len(description) < 180:
            body_text = clean(soup.get_text(" "))
            if len(body_text) > len(description):
                description = body_text[:30000]

        return {
            "url": final_url,
            "title": title or fallback_title,
            "location": location,
            "posted_date": posted_date,
            "description": description,
            "source_type": direct_type,
            "match_score": 1.0,
        }
    except Exception:
        return None


def _candidate_link_score(candidate_url, anchor_text, company, title):
    direct_type = classify_direct_job_url(candidate_url)
    if not direct_type:
        return -1

    score = {
        "workday": 120,
        "icims": 120,
        "oracle": 120,
        "taleo": 120,
        "eightfold": 120,
        "ashby": 115,
        "lever": 115,
        "greenhouse": 115,
        "smartrecruiters": 115,
        "company-careers": 80,
    }.get(direct_type, 70)

    anchor_n = norm(anchor_text)
    url_n = norm(urllib.parse.unquote(candidate_url))
    title_n = norm(title)
    company_n = norm(company)

    if any(word in anchor_n for word in ("apply", "view job", "job details", "position")):
        score += 15
    if title_n:
        score += int(25 * title_similarity(title_n, f"{anchor_n} {url_n}"))
    if company_n and company_n in url_n:
        score += 8
    return score


def direct_from_provider_page(url, company="", title=""):
    if not url:
        return None

    def resolve_candidate(candidate_url):
        if not candidate_url:
            return None
        try:
            rr = SESSION.get(candidate_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if rr.status_code >= 400:
                return None
            final = canonical_url(rr.url)
            if not is_direct_ats(final):
                return None
            return fetch_direct_job_details(final, title) or {
                "url": final,
                "title": title,
                "location": "",
                "posted_date": None,
                "description": "",
                "source_type": source_type(final),
                "match_score": 1.0,
            }
        except Exception:
            return None

    try:
        r = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        r.raise_for_status()

        final_url = canonical_url(r.url)
        if is_direct_ats(final_url):
            return fetch_direct_job_details(final_url, title) or {
                "url": final_url,
                "title": title,
                "location": "",
                "posted_date": None,
                "description": "",
                "source_type": source_type(final_url),
                "match_score": 1.0,
            }

        soup = BeautifulSoup(r.text, "html.parser")
        direct_candidates = []
        redirect_candidates = []
        seen = set()

        for link in soup.find_all("a", href=True):
            href = urllib.parse.urljoin(r.url, link.get("href"))
            candidate = canonical_url(href)
            if candidate in seen:
                continue
            seen.add(candidate)

            anchor = clean(link.get_text(" "))
            anchor_n = norm(anchor)

            if is_direct_ats(candidate):
                score = _candidate_link_score(candidate, anchor, company, title)
                direct_candidates.append((score, candidate))
                continue

            # Job boards often hide the employer URL behind an internal Apply
            # redirect. Follow only links whose text clearly represents a job
            # application/view action; never store the provider URL itself.
            if any(term in anchor_n for term in (
                "apply", "apply now", "apply for this job", "view job",
                "view company", "original listing", "company website",
                "employer site", "career site"
            )):
                redirect_candidates.append(candidate)

        direct_candidates.sort(key=lambda item: item[0], reverse=True)
        for _, candidate in direct_candidates[:10]:
            details = fetch_direct_job_details(candidate, title)
            if not details:
                continue
            actual_title = details.get("title") or title
            if title and actual_title and title_similarity(title, actual_title) < 0.48:
                continue
            return details

        for candidate in redirect_candidates[:12]:
            details = resolve_candidate(candidate)
            if not details:
                continue
            actual_title = details.get("title") or title
            if title and actual_title and title_similarity(title, actual_title) < 0.48:
                continue
            return details

    except Exception:
        pass

    return None

def _bing_rss_links(query):
    try:
        r = SESSION.get(
            "https://www.bing.com/search",
            params={"q": query, "format": "rss"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        out = []
        for item in root.findall(".//item"):
            link = clean(item.findtext("link") or "")
            title = clean(item.findtext("title") or "")
            if link:
                out.append((link, title))
        return out
    except Exception:
        return []


def expanded_direct_search_resolve(company, title):
    # Best-effort fallback for ATS families without a simple public board API
    # (Workday, iCIMS, Oracle/Taleo, Eightfold) and branded company-careers pages.
    # Run several focused searches because provider feeds often use an old brand
    # name or a title variant that differs slightly from the company careers page.
    company_names = [company]
    company_names.extend(COMPANY_NAME_ALIASES.get(norm(company), []))

    title_variants = [title]
    simplified = re.sub(r"\s*[-–—]\s*(?:cmeg|public sector|americas|us|usa)\s*$", "", title, flags=re.I).strip()
    if simplified and simplified.lower() != title.lower():
        title_variants.append(simplified)

    queries = []
    for company_name in company_names:
        for title_variant in title_variants:
            queries.extend([
                f'"{company_name}" "{title_variant}" careers',
                f'"{company_name}" "{title_variant}" greenhouse',
                f'"{company_name}" "{title_variant}" workday',
                f'"{company_name}" "{title_variant}" jobs',
            ])

    candidates = []
    seen = set()

    for query in dict.fromkeys(queries):
        for link, result_title in _bing_rss_links(query):
            candidate = canonical_url(link)
            if candidate in seen or not is_direct_ats(candidate):
                continue
            seen.add(candidate)
            score = _candidate_link_score(candidate, result_title, company, title)
            candidates.append((score, candidate))

    candidates.sort(key=lambda item: item[0], reverse=True)
    for _, candidate in candidates[:20]:
        details = fetch_direct_job_details(candidate, title)
        if not details:
            continue
        actual_title = details.get("title") or title
        if title_similarity(title, actual_title) < 0.50:
            continue
        return details

    return None

def ashby_resolve(company, title):
    for board in company_slug_variants(company):
        url = (
            "https://api.ashbyhq.com/posting-api/job-board/"
            + urllib.parse.quote(board, safe="")
        )

        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT)

            if r.status_code != 200:
                continue

            jobs = r.json().get("jobs", [])

            match = best_match(
                title,
                jobs,
                lambda j: j.get("title") or "",
            )

            if not match:
                continue

            score, job = match

            job_url = (
                job.get("jobUrl")
                or job.get("applyUrl")
            )

            if not job_url or not is_direct_ats(job_url):
                continue

            return {
                "url": canonical_url(job_url),
                "title": job.get("title") or title,
                "location": job.get("location") or "",
                "posted_date": iso_date(job.get("publishedAt")),
                "description": clean(
                    BeautifulSoup(
                        job.get("descriptionHtml") or "",
                        "html.parser",
                    ).get_text(" ")
                ),
                "source_type": "ashby",
                "match_score": score,
            }

        except Exception:
            continue

    return None

def lever_resolve(company, title):
    for site in company_slug_variants(company):
        url = (
            "https://api.lever.co/v0/postings/"
            + urllib.parse.quote(site, safe="")
        )

        try:
            r = SESSION.get(
                url,
                params={"mode": "json"},
                timeout=REQUEST_TIMEOUT,
            )

            if r.status_code != 200:
                continue

            postings = r.json()

            if not isinstance(postings, list):
                continue

            match = best_match(
                title,
                postings,
                lambda j: j.get("text") or "",
            )

            if not match:
                continue

            score, job = match

            posting_id = str(job.get("id") or "").strip()

            if not posting_id:
                continue

            direct_url = f"https://jobs.lever.co/{site}/{posting_id}"

            location = (
                job.get("categories")
                or {}
            ).get("location", "")

            return {
                "url": canonical_url(direct_url),
                "title": job.get("text") or title,
                "location": location,
                "posted_date": iso_date(job.get("createdAt")),
                "description": clean(
                    job.get("descriptionPlain")
                    or job.get("description")
                    or ""
                ),
                "source_type": "lever",
                "match_score": score,
            }

        except Exception:
            continue

    return None

def greenhouse_resolve(company, title):
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

            postings = r.json().get("jobs", [])

            match = best_match(
                title,
                postings,
                lambda j: j.get("title") or "",
            )

            if not match:
                continue

            score, job = match

            direct_url = job.get("absolute_url") or ""

            # Greenhouse often returns a branded company careers URL with
            # ?gh_jid=<id> instead of job-boards.greenhouse.io. Treat that as a
            # direct Greenhouse job and preserve the job-identifying query.
            if not direct_url:
                continue
            if not is_direct_ats(direct_url):
                parsed_qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(direct_url).query))
                if not parsed_qs.get("gh_jid"):
                    continue

            location = (
                job.get("location")
                or {}
            ).get("name", "")

            return {
                "url": canonical_url(direct_url),
                "title": job.get("title") or title,
                "location": location,
                "posted_date": iso_date(job.get("updated_at")),
                "description": clean(
                    BeautifulSoup(
                        job.get("content") or "",
                        "html.parser",
                    ).get_text(" ")
                ),
                "source_type": "greenhouse",
                "match_score": score,
            }

        except Exception:
            continue

    return None

def smartrecruiters_resolve(company, title):
    for company_id in company_slug_variants(company):
        url = (
            "https://api.smartrecruiters.com/v1/companies/"
            f"{company_id}/postings"
        )

        try:
            r = SESSION.get(
                url,
                params={"limit": 100},
                timeout=REQUEST_TIMEOUT,
            )

            if r.status_code != 200:
                continue

            postings = r.json().get("content", [])

            match = best_match(
                title,
                postings,
                lambda j: j.get("name") or "",
            )

            if not match:
                continue

            score, job = match

            posting_id = str(job.get("id") or "").strip()

            if not posting_id:
                continue

            job_title = job.get("name") or title

            title_slug = re.sub(
                r"[^a-z0-9]+",
                "-",
                norm(job_title),
            ).strip("-")

            direct_url = (
                "https://jobs.smartrecruiters.com/"
                f"{company_id}/{posting_id}-{title_slug}"
            )

            loc = job.get("location") or {}

            location = ", ".join(
                value
                for value in [
                    loc.get("city"),
                    loc.get("region"),
                    loc.get("country"),
                ]
                if value
            )

            return {
                "url": canonical_url(direct_url),
                "title": job_title,
                "location": location,
                "posted_date": iso_date(job.get("releasedDate")),
                "description": "",
                "source_type": "smartrecruiters",
                "match_score": score,
            }

        except Exception:
            continue

    return None

def resolve_direct_ats(company, title, provider_url):
    direct = direct_from_provider_page(provider_url, company, title)

    if direct:
        return direct

    for resolver in [
        ashby_resolve,
        lever_resolve,
        greenhouse_resolve,
        smartrecruiters_resolve,
    ]:
        result = resolver(company, title)

        if result:
            # Enrich API-resolved postings with page metadata when possible.
            enriched = fetch_direct_job_details(result.get("url") or "", result.get("title") or title)
            if enriched:
                for key in ("location", "posted_date", "description"):
                    if enriched.get(key):
                        result[key] = enriched[key]
                result["url"] = enriched.get("url") or result["url"]
                result["source_type"] = enriched.get("source_type") or result.get("source_type")
            return result

    return expanded_direct_search_resolve(company, title)

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

            for job in r.json().get("jobs", []):
                jid = str(job.get("id") or "")

                if jid and jid in seen:
                    continue

                if jid:
                    seen.add(jid)

                yield {
                    "company": clean(job.get("companyName") or ""),
                    "title": clean(job.get("jobTitle") or ""),
                    "url": job.get("url") or "",
                    "location": clean(job.get("jobGeo") or "Remote"),
                    "posted_date": iso_date(job.get("pubDate")),
                    "description": clean(
                        BeautifulSoup(
                            job.get("jobDescription") or "",
                            "html.parser",
                        ).get_text(" ")
                    ),
                }

        except Exception as e:
            print("Jobicy error:", e)

def remotive():
    try:
        r = SESSION.get(
            "https://remotive.com/api/remote-jobs",
            timeout=25,
        )

        r.raise_for_status()

        for job in r.json().get("jobs", []):
            yield {
                "company": clean(job.get("company_name") or ""),
                "title": clean(job.get("title") or ""),
                "url": job.get("url") or "",
                "location": clean(
                    job.get("candidate_required_location") or "Remote"
                ),
                "posted_date": iso_date(job.get("publication_date")),
                "description": clean(
                    BeautifulSoup(
                        job.get("description") or "",
                        "html.parser",
                    ).get_text(" ")
                ),
            }

    except Exception as e:
        print("Remotive error:", e)

def remoteok():
    try:
        r = SESSION.get(
            "https://remoteok.com/api",
            timeout=25,
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, list) and data and not data[0].get("position"):
            data = data[1:]

        for job in data if isinstance(data, list) else []:
            yield {
                "company": clean(job.get("company") or ""),
                "title": clean(job.get("position") or ""),
                "url": job.get("url") or job.get("apply_url") or "",
                "location": clean(job.get("location") or "Remote"),
                "posted_date": iso_date(job.get("date")),
                "description": clean(
                    BeautifulSoup(
                        job.get("description") or "",
                        "html.parser",
                    ).get_text(" ")
                ),
            }

    except Exception as e:
        print("RemoteOK error:", e)

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

def existing_keys():
    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "source_key",
            "user_id": f"eq.{USER_ID}",
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

def update_existing_match(key, match_score, match_summary, last_seen_at, salary_min=None, salary_max=None):
    headers = {
        **supabase_headers(),
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    # Never erase an already-known salary just because one resolver did not
    # return compensation on a later run.
    payload = {
        "match_score": match_score,
        "match_summary": match_summary,
        "last_seen_at": last_seen_at,
    }
    if salary_min is not None:
        payload["salary_min"] = salary_min
    if salary_max is not None:
        payload["salary_max"] = salary_max

    r = SESSION.patch(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=headers,
        params={
            "user_id": f"eq.{USER_ID}",
            "source_key": f"eq.{key}",
        },
        data=json.dumps(payload),
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase match update failed {r.status_code}: {r.text}")


def backfill_existing_matches():
    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "id,company,role,job_url,description,location,match_score,match_summary,salary_min,salary_max",
            "user_id": f"eq.{USER_ID}",
            "limit": "10000",
        },
        timeout=30,
    )
    r.raise_for_status()

    updated = 0
    comp_updated = 0

    for row in r.json():
        job_id = row.get("id")
        if not job_id:
            continue

        stored_description = row.get("description") or ""
        description = stored_description

        # Existing rows were often created before compensation extraction existed.
        # Re-fetch the direct posting when compensation is missing so we are not
        # limited to an older/truncated description already stored in Supabase.
        needs_salary_refresh = row.get("salary_min") is None or row.get("salary_max") is None
        refreshed = None
        if needs_salary_refresh and row.get("job_url"):
            refreshed = fetch_direct_job_details(
                row.get("job_url") or "",
                row.get("role") or "",
            )
            fresh_description = (refreshed or {}).get("description") or ""
            if len(fresh_description) > len(description):
                description = fresh_description

        score, summary = calculate_match(
            row.get("role") or "",
            description,
            row.get("location") or "",
        )

        if not description:
            summary = (
                summary
                + " Fit is provisional because the stored posting does not yet contain full JD text."
            )

        salary_min, salary_max = extract_base_compensation(description)

        payload = {
            "match_score": score,
            "match_summary": summary,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        }

        # Preserve richer JD text obtained from the direct posting so later match
        # and compensation backfills do not have to start from a truncated copy.
        if description and description != stored_description:
            payload["description"] = description

        if salary_min is not None:
            payload["salary_min"] = salary_min
        if salary_max is not None:
            payload["salary_max"] = salary_max

        needs_match = row.get("match_score") is None or not row.get("match_summary")
        needs_description = bool(description and description != stored_description)
        needs_comp = (
            salary_min is not None and salary_max is not None and
            (row.get("salary_min") != salary_min or row.get("salary_max") != salary_max)
        )

        if not needs_match and not needs_description and not needs_comp:
            continue

        pr = SESSION.patch(
            f"{SUPABASE_URL}/rest/v1/discovered_jobs",
            headers={
                **supabase_headers(),
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params={
                "id": f"eq.{job_id}",
                "user_id": f"eq.{USER_ID}",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        if pr.status_code >= 300:
            raise RuntimeError(
                f"Supabase match/compensation backfill failed {pr.status_code}: {pr.text}"
            )

        updated += 1
        if needs_comp:
            comp_updated += 1
            print(
                "BACKFILLED COMP:",
                row.get("company") or "",
                "|",
                row.get("role") or "",
                "|",
                f"${salary_min:,} - ${salary_max:,}",
            )

    if updated:
        print(f"Backfilled match/description data for {updated} existing discovery rows.")
    if comp_updated:
        print(f"Backfilled compensation for {comp_updated} existing discovery rows.")


def purge_existing_active_ts():
    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "id,role,description",
            "user_id": f"eq.{USER_ID}",
            "limit": "10000",
        },
        timeout=30,
    )
    r.raise_for_status()

    purge_ids = [
        row["id"]
        for row in r.json()
        if row.get("id") and requires_active_ts(row.get("role"), row.get("description"))
    ]

    for job_id in purge_ids:
        dr = SESSION.delete(
            f"{SUPABASE_URL}/rest/v1/discovered_jobs",
            headers=supabase_headers(),
            params={
                "id": f"eq.{job_id}",
                "user_id": f"eq.{USER_ID}",
            },
            timeout=30,
        )
        if dr.status_code >= 300:
            raise RuntimeError(f"Supabase active-TS purge failed {dr.status_code}: {dr.text}")

    if purge_ids:
        print(f"Purged {len(purge_ids)} existing active-TS discovery rows.")


def insert_rows(rows):
    if not rows:
        return

    headers = {
        **supabase_headers(),
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=ignore-duplicates",
    }

    for i in range(0, len(rows), 100):
        batch = rows[i:i + 100]

        r = SESSION.post(
            f"{SUPABASE_URL}/rest/v1/discovered_jobs",
            headers=headers,
            data=json.dumps(batch),
            timeout=30,
        )

        if r.status_code >= 300:
            raise RuntimeError(
                f"Supabase insert failed {r.status_code}: {r.text}"
            )

def main():
    cfg = load_config()

    max_age_days = int(
        cfg.get(
            "max_post_age_days",
            30,
        )
    )

    purge_existing_active_ts()
    backfill_existing_matches()
    known = existing_keys()

    raw = []

    raw.extend(
        list(
            jobicy(cfg)
        )
    )

    raw.extend(
        list(
            remotive()
        )
    )

    raw.extend(
        list(
            remoteok()
        )
    )

    candidates = {}

    for job in raw:
        if not job["company"] or not job["title"]:
            continue

        if company_is_excluded(
            job["company"],
            cfg,
        ):
            print(
                "EXCLUDED COMPANY:",
                job["company"],
                "|",
                job["title"],
            )
            continue

        if not title_matches_config(
            job["title"],
            cfg,
        ):
            continue

        if requires_active_ts(job.get("title"), job.get("description")):
            print(
                "EXCLUDED ACTIVE TS:",
                job["company"],
                "|",
                job["title"],
            )
            continue

        if not location_matches(
            job["location"],
            cfg,
        ):
            print(
                "EXCLUDED LOCATION:",
                job["company"],
                "|",
                job["title"],
                "|",
                job["location"],
            )
            continue

        if is_too_old(
            job.get("posted_date"),
            max_age_days,
        ):
            print(
                "STALE CANDIDATE:",
                job["company"],
                "|",
                job["title"],
                "|",
                job.get("posted_date"),
            )
            continue

        key = (
            norm(job["company"]),
            norm(job["title"]),
        )

        if key not in candidates:
            candidates[key] = job

    print(
        "Candidate jobs after filters:",
        len(candidates),
    )

    rows = []

    resolved = 0
    unresolved = 0
    stale = 0
    location_rejected = 0
    duplicates = 0

    for job in candidates.values():
        company = job["company"]
        title = job["title"]

        ats = resolve_direct_ats(
            company,
            title,
            job.get("url") or "",
        )

        if not ats:
            unresolved += 1

            print(
                "UNRESOLVED:",
                company,
                "|",
                title,
            )

            continue

        direct_url = canonical_url(
            ats["url"]
        )

        if not is_direct_ats(
            direct_url
        ):
            unresolved += 1
            continue

        resolved_location = (
            ats.get("location")
            or job.get("location")
            or ""
        )

        # Final location validation after ATS resolution.
        if not location_matches(
            resolved_location,
            cfg,
        ):
            location_rejected += 1

            print(
                "REJECTED ATS LOCATION:",
                company,
                "|",
                title,
                "|",
                resolved_location,
            )

            continue

        resolved_posted_date = (
            ats.get("posted_date")
            or job.get("posted_date")
        )

        if is_too_old(
            resolved_posted_date,
            max_age_days,
        ):
            stale += 1

            print(
                "STALE ATS POSTING:",
                company,
                "|",
                title,
                "|",
                resolved_posted_date,
            )

            continue

        key = source_key(
            direct_url
        )

        is_existing = key in known

        resolved_title = (
            ats.get("title")
            or title
        )

        resolved_description = (
            ats.get("description")
            or job.get("description")
            or ""
        )

        if requires_active_ts(resolved_title, resolved_description):
            print(
                "EXCLUDED ACTIVE TS AFTER ATS RESOLUTION:",
                company,
                "|",
                resolved_title,
            )
            continue

        match_score, match_summary = calculate_match(
            resolved_title,
            resolved_description,
            resolved_location,
        )
        salary_min, salary_max = extract_base_compensation(resolved_description)

        now_iso = datetime.now(timezone.utc).isoformat()

        if is_existing:
            update_existing_match(
                key,
                match_score,
                match_summary,
                now_iso,
                salary_min,
                salary_max,
            )
            duplicates += 1
            print(
                "UPDATED MATCH:",
                company,
                "|",
                resolved_title,
                "|",
                f"{match_score}%",
            )
            continue

        rows.append({
            "user_id": USER_ID,
            "company": company,
            "role": resolved_title,
            "job_url": direct_url,
            "posted_date": resolved_posted_date,
            "location": resolved_location or None,
            "work_arrangement": (
                "Remote"
                if "remote" in norm(resolved_location)
                else "Hybrid"
                if "hybrid" in norm(resolved_location)
                else None
            ),
            "match_score": match_score,
            "match_summary": match_summary,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "source_site": (
                f"{company} Careers · "
                f"{ats.get('source_type', source_type(direct_url)).title()}"
            ),
            "description": resolved_description or None,
            "external_job_id": direct_url.rstrip("/").split("/")[-1],
            "source_type": ats.get(
                "source_type",
                source_type(direct_url),
            ),
            "source_key": key,
            "decision": "new",
            "first_seen_at": now_iso,
            "last_seen_at": now_iso,
        })

        known.add(key)

        resolved += 1

        print(
            "RESOLVED:",
            company,
            "|",
            resolved_title,
            "|",
            resolved_location,
            "|",
            resolved_posted_date,
            "->",
            direct_url,
        )

    insert_rows(
        rows
    )

    print("")
    print("========== JOB DISCOVERY SUMMARY ==========")
    print("Candidates checked:", len(candidates))
    print("Resolved direct ATS postings:", resolved)
    print("Stale postings skipped:", stale)
    print("Location-rejected postings:", location_rejected)
    print("Already known / duplicates:", duplicates)
    print("Unresolved candidates skipped:", unresolved)
    print("Inserted new direct-company/ATS jobs:", len(rows))
    print("===========================================")

if __name__ == "__main__":
    main()
