#!/usr/bin/env python3

import os
import re
import json
import html
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
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
    "apply.workable.com": "workable",
    "app.eightfold.ai": "eightfold",
    "careers.jobvite.com": "jobvite",
    "jobs.jobvite.com": "jobvite",
    "join.com": "join",
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
    "himalayas.",
    "weworkremotely.",
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

    if host.endswith(".recruitee.com"):
        return "recruitee"

    if host.endswith(".breezy.hr"):
        return "breezy"

    if host.endswith(".bamboohr.com"):
        return "bamboohr"

    if host.endswith(".jobs.personio.de") or host.endswith(".jobs.personio.com"):
        return "personio"

    if host.endswith(".teamtailor.com"):
        return "teamtailor"

    if host.endswith(".workable.com") or host == "apply.workable.com":
        return "workable"

    if host.endswith(".jobvite.com"):
        return "jobvite"

    if host == "join.com" or host.endswith(".join.com"):
        return "join"

    if host.endswith(".rippling.com"):
        return "rippling"

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


def feed_date(value):
    direct = iso_date(value)
    if direct:
        return direct
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        return parsedate_to_datetime(str(value)).date().isoformat()
    except Exception:
        return None

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

HARD_EXCLUDED_COMPANIES = (
    "databricks",
)

def company_is_excluded(company, cfg):
    normalized = norm(company)

    # Permanent hard exclusions that should never enter Discovery.
    # Keep these independent of config so scheduled imports, cleanup, and
    # supplemental discovery all enforce them consistently.
    for excluded in HARD_EXCLUDED_COMPANIES:
        target = norm(excluded)
        if target and (normalized == target or target in normalized):
            return True

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

    # "Forward Deployed Engineering" describes a department/function and should
    # not, by itself, convert a generic software/AI/agent engineer title into
    # the customer-facing Forward Deployed Engineer role Toni is targeting.
    if "forward deployed engineering" in title_n:
        primary = title_n.split("forward deployed engineering", 1)[0].strip()
        if "forward deployed engineer" not in primary:
            return False

    # Generic engineering titles that only mention a target discipline in
    # parentheses or org text should not pass.
    generic_engineering_markers = (
        "software engineer", "frontend engineer", "front end engineer",
        "backend engineer", "back end engineer", "mobile engineer",
        "qa engineer", "quality engineer", "data engineer",
        "devops engineer", "site reliability engineer", "sre",
        "machine learning engineer", "ml engineer",
        "ai engineer", "agents engineer", "agent engineer",
    )
    if any(marker in title_n for marker in generic_engineering_markers):
        # Allow only when the actual title itself is explicitly one of the
        # customer-facing target titles.
        explicit_customer_title = any(
            phrase in title_n
            for phrase in (
                "solutions engineer", "sales engineer", "solutions architect",
                "customer solutions engineer", "customer engineer",
                "forward deployed engineer", "presales engineer",
                "pre sales engineer", "security solutions engineer",
                "cloud solutions architect", "public sector solutions engineer",
                "federal solutions engineer",
            )
        )
        if not explicit_customer_title:
            return False

    for wanted in cfg.get("titles", []):
        target = norm(wanted)
        if not target:
            continue

        # Exact phrase containment is fine for most target titles.
        if target in title_n or title_n in target:
            # Special-case FDE so "forward deployed engineering" cannot satisfy
            # "forward deployed engineer".
            if target == "forward deployed engineer":
                if not re.search(r"\bforward deployed engineer\b", title_n):
                    continue
            return True

    return False

def _has_location_phrase(location_n, phrase):
    phrase_n = norm(phrase)
    if not phrase_n:
        return False
    return f" {phrase_n} " in f" {location_n} "




def _is_generic_us_location(location_n):
    compact = re.sub(r"\s+", " ", location_n).strip()
    return compact in {
        "united states", "usa", "us", "u s",
        "united states of america",
    }



NON_DMV_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "washington", "west virginia",
    "wisconsin", "wyoming",
)

NON_DMV_STATE_CODES = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "ma", "mi", "mn", "ms", "mo",
    "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or",
    "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "wa", "wv", "wi", "wy",
)


def _location_has_non_dmv_state(location=""):
    raw = clean(location).lower()
    normalized = norm(location)

    if any(name in normalized for name in NON_DMV_STATE_NAMES):
        return True

    # State abbreviations must appear as standalone location tokens to avoid
    # accidental matches inside normal words.
    for code in NON_DMV_STATE_CODES:
        if re.search(rf"(?:^|[\s,;/()\-]){re.escape(code)}(?:$|[\s,;/()\-])", raw):
            return True

    return False


def _location_field_scope(location=""):
    """Classify the ATS location field itself.

    Returns:
      dmv             - DC/MD/VA geography
      us_remote       - explicit nationwide/U.S.-remote option
      state_remote    - remote but tied to a non-DMV state/region
      foreign_remote  - remote but tied to a foreign geography
      generic_us      - broad United States/USA/US with no remote marker
      other           - anything else
    """
    raw = clean(location).lower()
    location_n = norm(location)

    if not location_n:
        return "other"

    if _dmv_evidence(location, ""):
        return "dmv"

    # Split multi-location strings into option-like segments. This lets
    # "New York, NY; Remote, USA" qualify because one distinct option is
    # nationwide remote, while "Texas-Remote, United States" remains
    # state-restricted remote.
    segments = [
        seg.strip()
        for seg in re.split(r"[;|•]+", raw)
        if seg.strip()
    ] or [raw]

    foreign_markers = (
        "canada", "united kingdom", " uk", "germany", "france", "spain",
        "italy", "japan", "singapore", "australia", "india", "ireland",
        "netherlands", "belgium", "sweden", "south korea", "mexico",
        "brazil", "chile", "colombia", "peru", "argentina", "poland",
        "europe", "emea", "apac", "latam",
    )

    broad_us_remote_patterns = (
        r"^\s*remote\s*[-,:/ ]+\s*(?:us|usa|united states)\s*$",
        r"^\s*(?:us|usa|united states)\s*[-,:/ ]+\s*remote\s*$",
        r"^\s*us[- ]remote\s*$",
        r"^\s*remote[- ]us\s*$",
        r"^\s*anywhere,\s*(?:us|usa|united states)\s*$",
        r"^\s*anywhere in (?:the )?(?:us|usa|united states)\s*$",
        r"^\s*nationwide(?:\s*remote)?\s*$",
    )

    # First: if any separate segment is explicitly nationwide U.S. remote,
    # the posting has an eligible U.S.-remote option.
    for seg in segments:
        if any(re.search(p, seg, re.I) for p in broad_us_remote_patterns):
            return "us_remote"

    # Common whole-field broad remote variants with punctuation/order oddities.
    if raw.strip() in {
        "remote", "us remote", "usa remote", "united states remote",
        "remote us", "remote usa", "remote united states",
        "us-remote", "remote-us",
    }:
        return "us_remote"

    # Next: state-restricted remote outside DMV must be rejected, even if the
    # same segment also says "United States".
    if "remote" in location_n and _location_has_non_dmv_state(location):
        return "state_remote"

    # Foreign-only remote.
    if "remote" in location_n and any(marker.strip() in location_n for marker in foreign_markers):
        return "foreign_remote"

    # Generic national labels are evaluated against the direct JD later.
    if _is_generic_us_location(location_n):
        return "generic_us"

    # "Anywhere, US" and similar without the word remote still denote nationwide.
    if re.fullmatch(r"\s*anywhere,?\s*(?:us|usa|united states)\s*", raw, re.I):
        return "us_remote"

    return "other"


DMV_TERMS = (
    "washington dc", "washington d c", "district of columbia", "dc metro", "dmv",
    "maryland", "md", "virginia", "va",
    "arlington", "alexandria", "mclean", "tysons", "reston", "herndon",
    "fairfax", "falls church", "bethesda", "rockville", "silver spring",
    "gaithersburg", "columbia md", "baltimore", "waldorf",
)


def _dmv_evidence(location="", description=""):
    text = norm(" ".join([location or "", description or ""]))
    padded = f" {text} "
    for term in DMV_TERMS:
        term_n = norm(term)
        if len(term_n) <= 2:
            if f" {term_n} " in padded:
                return True
        elif term_n in text:
            return True
    return False


def _onsite_evidence(location="", description=""):
    text = norm(" ".join([location or "", description or ""]))
    phrases = (
        "onsite", "on site", "on-site",
        "in office", "in-office", "work from the office",
        "work in the office", "office days", "days in office",
        "days per week in office", "days a week in office",
        "commutable distance", "commute distance",
        "hub location", "hub locations",
        "work onsite", "work on site", "work on-site",
        "report to the office",
    )
    return any(p in text for p in phrases)


def _strong_remote_evidence(location="", description=""):
    """Remote evidence strong enough to treat the role as genuinely remote.

    Intentionally does NOT treat phrases such as "work remotely on Mondays"
    as fully remote.
    """
    location_n = norm(location)
    description_n = norm(description)

    location_remote = any(
        phrase in location_n
        for phrase in (
            "remote", "remote us", "remote usa", "remote united states",
            "united states remote", "us remote", "u s remote",
            "remote - us", "remote - usa", "remote - united states",
            "remote within the united states",
        )
    )

    description_remote = any(
        phrase in description_n
        for phrase in (
            "fully remote", "fully-remote", "100 remote", "100% remote",
            "remote role", "remote position",
            "this role is remote", "this position is remote",
            "remote within the united states", "remote in the united states",
            "us remote", "usa remote", "remote us", "remote usa",
            "work from anywhere in the united states",
            "work from anywhere in the us",
            "li remote", "li-remote",
        )
    )

    return location_remote or description_remote


def _hybrid_evidence(location="", description=""):
    text = norm(" ".join([location or "", description or ""]))

    explicit_hybrid = any(
        phrase in text
        for phrase in (
            "hybrid", "li hybrid", "li-hybrid",
            "hybrid schedule", "hybrid work",
        )
    )
    if explicit_hybrid:
        return True

    onsite = _onsite_evidence(location, description)
    partial_remote = any(
        phrase in text
        for phrase in (
            "remotely on", "remote on monday", "remote monday",
            "work remotely one day", "work remotely two days",
            "work from home one day", "work from home two days",
            "flexibility to work remotely", "flexible remote day",
        )
    )
    return onsite and partial_remote


def infer_work_arrangement(location="", description=""):
    # Hybrid/onsite obligations must win over weak remote-day language.
    if _hybrid_evidence(location, description):
        return "Hybrid"

    if _onsite_evidence(location, description):
        return "Onsite"

    if _strong_remote_evidence(location, description):
        return "Remote"

    return None


def location_matches(location, cfg, description=""):
    """Final geography gate.

    Keep only:
      - explicit nationwide/U.S.-remote roles
      - generic U.S. locations whose DIRECT JD clearly confirms U.S.-remote
      - DC/MD/VA roles that are actually remote or hybrid

    Reject:
      - onsite roles
      - non-DMV state/city restricted roles
      - state-restricted remote outside DMV
      - foreign-only roles
    """
    if not cfg.get("us_only", True):
        return True

    scope = _location_field_scope(location)

    # Explicit nationwide U.S.-remote ATS wording is authoritative.
    if scope == "us_remote":
        return True

    if scope in ("state_remote", "foreign_remote", "other"):
        return False

    if scope == "dmv":
        arrangement = infer_work_arrangement(location, description)
        return arrangement in ("Remote", "Hybrid")

    if scope == "generic_us":
        # Generic "United States" needs direct-JD proof of true remote work.
        return _strong_remote_evidence(location, description) and not _onsite_evidence(location, description)

    return False

def requires_active_ts(*parts):
    raw_text = " ".join(clean(part) for part in parts if part).lower()
    if not raw_text:
        return False

    title_text = clean(parts[0] if parts else "").lower()

    # A title literally labeled "Clearance Required" is treated as a blocker
    # unless it explicitly says Public Trust.
    if (
        ("clearance required" in title_text or "required clearance" in title_text)
        and "public trust" not in title_text
    ):
        return True

    fragments = re.split(r"[\n\r.!?;•]+", raw_text)
    obtain_terms = (
        "ability to obtain", "able to obtain", "eligible to obtain",
        "can obtain", "willing to obtain", "obtain a top secret",
        "obtain top secret", "sponsorship for", "sponsor for",
    )
    active_terms = (
        "active", "current", "currently hold", "must possess", "must hold",
        "required", "requirement", "requires", "possess a", "hold a",
        "must have", "need a",
    )

    for fragment in fragments:
        fragment_n = norm(fragment)
        has_ts = any(
            token in fragment_n
            for token in ("top secret", "ts sci", "tssci", "sci clearance")
        )
        if not has_ts:
            continue

        # Allow "able/eligible to obtain" unless the same sentence also says
        # the candidate must already hold an active/current clearance.
        if any(term in fragment for term in obtain_terms) and not any(
            term in fragment
            for term in ("active", "current", "currently hold", "must possess", "must hold")
        ):
            continue

        if any(term in fragment for term in active_terms):
            return True

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


def salary_missing_or_invalid(value):
    """Treat null/blank and obviously malformed annual salary values as unusable.

    Older discovery rows briefly stored values like 208 / 261 for $208k / $261k.
    Annual base salary values below $20k are therefore considered invalid and
    should be refreshed from the live posting rather than preserved.
    """
    if value is None or value == "":
        return True
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return True
    return amount <= 0 or amount < 20000


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



def greenhouse_job_details(company, title, url):
    """Fetch a full Greenhouse posting, including branded ?gh_jid= URLs."""
    try:
        parsed = urllib.parse.urlparse(url or "")
        qs = urllib.parse.parse_qs(parsed.query)

        board = ""
        job_id = ""

        m = re.search(
            r"(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io)/([^/?#]+)/jobs/(\d+)",
            url or "",
            re.I,
        )
        if m:
            board = m.group(1)
            job_id = m.group(2)

        if not job_id:
            vals = qs.get("gh_jid") or qs.get("gh_jid[]") or []
            if vals:
                job_id = str(vals[0]).strip()

        if not job_id:
            path_match = re.search(
                r"/careers/(?:jobs?/)?(\d{5,})(?:/|$)",
                parsed.path or "",
                re.I,
            )
            if path_match:
                job_id = path_match.group(1)

        if not job_id:
            return None

        boards = []
        if board:
            boards.append(board)
        boards.extend(company_slug_variants(company))

        seen = set()
        for candidate_board in boards:
            candidate_board = str(candidate_board or "").strip()
            key = candidate_board.lower()
            if not candidate_board or key in seen:
                continue
            seen.add(key)

            api_url = (
                "https://boards-api.greenhouse.io/v1/boards/"
                + urllib.parse.quote(candidate_board, safe="")
                + "/jobs/"
                + urllib.parse.quote(job_id, safe="")
            )

            r = SESSION.get(
                api_url,
                params={
                    "content": "true",
                    "pay_transparency": "true",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                continue

            job = r.json() or {}
            job_title = clean(job.get("title") or title or "")
            if title and job_title and title_similarity(title, job_title) < 0.55:
                continue

            content = clean(
                BeautifulSoup(
                    job.get("content") or "",
                    "html.parser",
                ).get_text(" ")
            )

            location = ""
            loc = job.get("location") or {}
            if isinstance(loc, dict):
                location = clean(loc.get("name") or "")
            elif loc:
                location = clean(loc)

            salary_min = None
            salary_max = None
            pay_ranges = job.get("pay_input_ranges") or []
            if isinstance(pay_ranges, list):
                # Prefer an explicitly base-salary/base-compensation range when present.
                ranked_ranges = []
                for pr in pay_ranges:
                    if not isinstance(pr, dict):
                        continue
                    currency = str(pr.get("currency_type") or "").upper()
                    if currency and currency != "USD":
                        continue
                    min_cents = pr.get("min_cents")
                    max_cents = pr.get("max_cents")
                    if min_cents is None or max_cents is None:
                        continue
                    try:
                        lo = int(round(float(min_cents) / 100.0))
                        hi = int(round(float(max_cents) / 100.0))
                    except (TypeError, ValueError):
                        continue
                    if lo <= 0 or hi <= 0:
                        continue
                    if lo > hi:
                        lo, hi = hi, lo
                    label = clean(
                        " ".join([
                            str(pr.get("title") or ""),
                            BeautifulSoup(str(pr.get("blurb") or ""), "html.parser").get_text(" "),
                        ])
                    ).lower()
                    # De-prioritize OTE/bonus/commission ranges when a base range exists.
                    if any(x in label for x in ("ote", "on-target", "on target", "bonus", "commission")):
                        priority = 2
                    elif any(x in label for x in ("base salary", "base compensation", "base pay", "annual salary", "salary range")):
                        priority = 0
                    else:
                        priority = 1
                    ranked_ranges.append((priority, lo, hi))
                if ranked_ranges:
                    ranked_ranges.sort(key=lambda x: (x[0], x[1], x[2]))
                    _, salary_min, salary_max = ranked_ranges[0]

            direct_url = canonical_url(
                job.get("absolute_url")
                or f"https://job-boards.greenhouse.io/{candidate_board}/jobs/{job_id}"
            )

            return {
                "url": direct_url,
                "title": job_title or title,
                "location": location,
                "posted_date": iso_date(job.get("updated_at")),
                "description": content,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "source_type": "greenhouse",
                "match_score": 1.0,
            }

    except Exception:
        return None

    return None


def fetch_direct_job_details(url, fallback_title="", company=""):
    gh = greenhouse_job_details(company, fallback_title, url)
    if gh and gh.get("description"):
        return gh

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
        company_name = clean(company or "")

        if jp:
            title = clean(jp.get("title") or fallback_title)
            description = clean(
                BeautifulSoup(str(jp.get("description") or ""), "html.parser").get_text(" ")
            )
            posted_date = iso_date(jp.get("datePosted"))
            location = _schema_location(jp)
            hiring_org = jp.get("hiringOrganization") or {}
            if isinstance(hiring_org, dict):
                company_name = clean(hiring_org.get("name") or company_name)

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
            "company": company_name,
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
            return fetch_direct_job_details(final, title, company) or {
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
            return fetch_direct_job_details(final_url, title, company) or {
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
            params={"q": query, "format": "rss", "count": 50},
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


def _bing_html_links(query):
    """Fallback to Bing HTML results when RSS is sparse or unavailable."""
    try:
        r = SESSION.get(
            "https://www.bing.com/search",
            params={"q": query, "count": 50},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for a in soup.select("li.b_algo h2 a[href], h2 a[href]"):
            link = clean(a.get("href") or "")
            title = clean(a.get_text(" "))
            if link.startswith("http"):
                out.append((link, title))
        return out
    except Exception:
        return []


def _unwrap_duckduckgo_url(url):
    try:
        parsed = urllib.parse.urlsplit(url or "")
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return urllib.parse.unquote(qs["uddg"][0])
    except Exception:
        pass
    return url


def _duckduckgo_html_links(query):
    """Independent no-key search fallback."""
    endpoints = (
        "https://html.duckduckgo.com/html/",
        "https://duckduckgo.com/html/",
    )
    for endpoint in endpoints:
        try:
            r = SESSION.get(endpoint, params={"q": query}, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            out = []
            for a in soup.select("a.result__a[href], .result h2 a[href]"):
                link = _unwrap_duckduckgo_url(clean(a.get("href") or ""))
                title = clean(a.get_text(" "))
                if link.startswith("http"):
                    out.append((link, title))
            if out:
                return out
        except Exception:
            continue
    return []


def _search_web_links(query):
    """Merge multiple independent search paths and dedupe canonical URLs."""
    providers = (
        ("bing-rss", _bing_rss_links),
        ("bing-html", _bing_html_links),
        ("duckduckgo-html", _duckduckgo_html_links),
    )
    merged = []
    seen = set()
    provider_counts = {}
    for provider_name, fn in providers:
        links = fn(query)
        provider_counts[provider_name] = len(links)
        for link, title in links:
            canonical = canonical_url(link)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            merged.append((canonical, title, provider_name))
    return merged, provider_counts


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
        search_results, _ = _search_web_links(query)
        for link, result_title, _provider in search_results:
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

def _company_hint_from_search(result_title, role_title, url):
    """Best-effort company name from search title or ATS board slug."""
    rt = clean(result_title or "")
    role_n = norm(role_title or "")

    # Common Bing titles look like "Company - Solutions Engineer".
    for sep in (" - ", " | ", " – ", " — "):
        if sep in rt:
            parts = [clean(x) for x in rt.split(sep) if clean(x)]
            for part in parts:
                if role_n and title_similarity(role_title, part) >= 0.72:
                    continue
                if any(k in norm(part) for k in ("jobs", "careers", "apply")) and len(parts) > 1:
                    continue
                if 1 <= len(part.split()) <= 8:
                    return part

    parsed = urllib.parse.urlsplit(url or "")
    host = parsed.netloc.lower().split(":")[0]
    parts = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
    slug = ""
    if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com", "job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.smartrecruiters.com"} and parts:
        slug = parts[0]
    if slug:
        words = re.sub(r"[_-]+", " ", slug).strip().split()
        return " ".join(w.capitalize() for w in words)
    return ""


def title_first_ats_search(cfg):
    """Discover direct ATS postings without needing a company/feed seed first.

    Search engines only discover candidate direct ATS URLs. Every result is then
    fetched from the employer/ATS and revalidated against the actual JD.
    """
    title_groups = [
        '("Solutions Engineer" OR "Sales Engineer" OR "Pre-Sales Engineer" OR "Presales Engineer")',
        '("Solutions Architect" OR "Customer Engineer" OR "Customer Solutions Engineer")',
        '("Forward Deployed Engineer" OR "Technical Solutions Engineer")',
        '("AI Solutions Engineer" OR "AI Solutions Architect" OR "Security Solutions Engineer")',
    ]
    domains = [
        "jobs.lever.co",
        "jobs.eu.lever.co",
        "jobs.ashbyhq.com",
        "job-boards.greenhouse.io",
        "boards.greenhouse.io",
        "jobs.smartrecruiters.com",
        "myworkdayjobs.com",
    ]

    out = []
    seen_urls = set()
    provider_totals = {"bing-rss": 0, "bing-html": 0, "duckduckgo-html": 0}
    raw_search_results = 0
    direct_urls = 0
    details_failed = 0
    title_rejected = 0
    company_rejected = 0
    clearance_rejected = 0
    location_rejected = 0

    for domain in domains:
        for titles in title_groups:
            # Do not require geography in the search-engine query. The live job
            # itself is the authority for geography and is filtered below.
            query = f"site:{domain} {titles}"
            search_results, provider_counts = _search_web_links(query)
            for provider, count in provider_counts.items():
                provider_totals[provider] = provider_totals.get(provider, 0) + count
            raw_search_results += len(search_results)

            for link, result_title, _provider in search_results:
                candidate_url = canonical_url(link)
                if not candidate_url or candidate_url in seen_urls:
                    continue
                if not is_direct_ats(candidate_url):
                    continue
                seen_urls.add(candidate_url)
                direct_urls += 1

                details = fetch_direct_job_details(candidate_url, "")
                if not details:
                    details_failed += 1
                    continue
                role = clean(details.get("title") or "")
                if not role or not title_matches_config(role, cfg):
                    title_rejected += 1
                    continue

                company = clean(details.get("company") or "")
                if not company:
                    company = _company_hint_from_search(result_title, role, candidate_url)
                if not company or company_is_excluded(company, cfg):
                    company_rejected += 1
                    continue

                description = clean(details.get("description") or "")
                location = clean(details.get("location") or "")
                if requires_active_ts(role, description):
                    clearance_rejected += 1
                    continue
                if not location_matches(location, cfg, description):
                    location_rejected += 1
                    continue

                out.append(_direct_candidate(
                    company,
                    role,
                    details.get("url") or candidate_url,
                    location,
                    details.get("posted_date"),
                    description,
                    "title-first-ats-search",
                ))

    print("========== TITLE-FIRST ATS SEARCH ==========")
    print("Bing RSS results:", provider_totals.get("bing-rss", 0))
    print("Bing HTML results:", provider_totals.get("bing-html", 0))
    print("DuckDuckGo HTML results:", provider_totals.get("duckduckgo-html", 0))
    print("Merged search results:", raw_search_results)
    print("Unique direct ATS URLs:", direct_urls)
    print("Direct job detail fetch failures:", details_failed)
    print("Title rejected:", title_rejected)
    print("Company rejected:", company_rejected)
    print("Active-TS rejected:", clearance_rejected)
    print("Location rejected:", location_rejected)
    print("Accepted title-first candidates:", len(out))
    print("===========================================")
    return out


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

            detailed = greenhouse_job_details(
                company,
                job.get("title") or title,
                direct_url,
            )
            if detailed:
                detailed["match_score"] = score
                if not detailed.get("location"):
                    detailed["location"] = location
                if not detailed.get("posted_date"):
                    detailed["posted_date"] = iso_date(job.get("updated_at"))
                return detailed

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
            enriched = fetch_direct_job_details(result.get("url") or "", result.get("title") or title, company)
            if enriched:
                for key in ("location", "posted_date", "description"):
                    if enriched.get(key):
                        result[key] = enriched[key]
                result["url"] = enriched.get("url") or result["url"]
                result["source_type"] = enriched.get("source_type") or result.get("source_type")
            return result

    # Do not fall back to search-engine indexing here. GitHub-hosted runners
    # have proven unreliable for Bing/DDG discovery. Supplemental ATS families
    # (iCIMS, Oracle/Taleo, Eightfold, generic company careers) are accepted when
    # a source feed hands us a direct employer URL or redirect.
    return None

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


def _himalayas_us_allowed(restrictions):
    """Return True when a Himalayas job is open to US applicants.

    Himalayas locationRestrictions is normally a list of objects like
    {"alpha2": "US", "name": "United States", "slug": "united-states"}.
    An empty list means worldwide, which is also US-eligible.
    """
    if not restrictions:
        return True

    if isinstance(restrictions, (str, dict)):
        restrictions = [restrictions]

    for item in restrictions:
        if isinstance(item, dict):
            values = (
                item.get("alpha2"),
                item.get("name"),
                item.get("slug"),
            )
        else:
            values = (item,)

        normalized = {norm(v) for v in values if v}
        if normalized & {
            "us", "usa", "united states",
            "united states of america", "united states america",
        }:
            return True

    return False


def himalayas(cfg):
    """Title-first remote discovery via Himalayas public no-auth API.

    Paginate each title-family search so discovery is not limited to page 1.
    """
    queries = [
        "solutions engineer",
        "solutions architect",
        "sales engineer",
        "forward deployed engineer",
        "technical solutions engineer",
        "customer engineer",
        "security solutions engineer",
        "ai solutions engineer",
    ]
    seen = set()
    total_pages = 0
    total_raw = 0
    accepted = 0

    for query in queries:
        previous_page_signature = None

        for page in range(1, 6):
            try:
                r = SESSION.get(
                    "https://himalayas.app/jobs/api/search",
                    params={
                        "q": query,
                        "country": "US",
                        "sort": "recent",
                        "page": page,
                    },
                    timeout=25,
                )
                r.raise_for_status()

                jobs = (r.json() or {}).get("jobs", []) or []
                total_pages += 1

                if not jobs:
                    break

                total_raw += len(jobs)

                signature = tuple(
                    clean(job.get("guid") or job.get("applicationLink") or "")
                    for job in jobs[:5]
                )
                if signature and signature == previous_page_signature:
                    break
                previous_page_signature = signature

                for job in jobs:
                    title = clean(job.get("title") or "")
                    company = clean(job.get("companyName") or "")
                    if not title or not company or not title_matches_config(title, cfg):
                        continue

                    restrictions = job.get("locationRestrictions") or []
                    if not _himalayas_us_allowed(restrictions):
                        continue

                    provider_url = clean(
                        job.get("applicationLink") or job.get("guid") or ""
                    )
                    if not provider_url:
                        continue

                    key = (
                        norm(company),
                        norm(title),
                        canonical_url(provider_url),
                    )
                    if key in seen:
                        continue
                    seen.add(key)

                    description = clean(
                        BeautifulSoup(
                            job.get("description") or job.get("excerpt") or "",
                            "html.parser",
                        ).get_text(" ")
                    )

                    accepted += 1
                    yield {
                        "company": company,
                        "title": title,
                        "url": provider_url,
                        "location": "Remote - United States",
                        "posted_date": feed_date(job.get("pubDate")),
                        "description": description,
                        "candidate_source": "himalayas",
                    }

            except Exception as e:
                print("Himalayas error:", query, "page", page, e)
                break

    print(
        "Himalayas discovery details:",
        f"pages={total_pages}",
        f"raw_jobs={total_raw}",
        f"accepted_target_jobs={accepted}",
    )


def weworkremotely(cfg):
    """WWR RSS feeds used only as provider seeds for direct ATS resolution."""
    feeds = [
        "https://weworkremotely.com/remote-jobs.rss",
        "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/all-other-remote-jobs.rss",
    ]

    seen = set()
    raw_items = 0
    accepted = 0

    for feed_url in feeds:
        try:
            r = SESSION.get(feed_url, timeout=25)
            r.raise_for_status()
            root = ET.fromstring(r.text)

            for item in root.findall(".//item"):
                raw_items += 1
                raw_title = clean(item.findtext("title") or "")
                provider_url = clean(item.findtext("link") or "")

                if ": " not in raw_title or not provider_url:
                    continue

                company, title = [clean(x) for x in raw_title.split(": ", 1)]
                if not company or not title or not title_matches_config(title, cfg):
                    continue

                key = (
                    norm(company),
                    norm(title),
                    canonical_url(provider_url),
                )
                if key in seen:
                    continue
                seen.add(key)

                description = clean(
                    BeautifulSoup(
                        item.findtext("description") or "",
                        "html.parser",
                    ).get_text(" ")
                )

                accepted += 1
                yield {
                    "company": company,
                    "title": title,
                    "url": provider_url,
                    "location": "Remote",
                    "posted_date": feed_date(item.findtext("pubDate") or ""),
                    "description": description,
                    "candidate_source": "weworkremotely",
                }

        except Exception as e:
            print("We Work Remotely error:", feed_url, e)

    print(
        "We Work Remotely discovery details:",
        f"raw_items={raw_items}",
        f"accepted_target_jobs={accepted}",
    )


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



def arbeitnow():
    """Public job-board feed used only for candidate discovery.

    Final insertion still requires resolution to a direct employer/ATS job URL.
    """
    for page in range(1, 4):
        try:
            r = SESSION.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                timeout=25,
            )
            r.raise_for_status()
            payload = r.json() or {}
            jobs = payload.get("data") or []
            if not jobs:
                break

            for job in jobs:
                created = job.get("created_at")
                posted_date = None
                if isinstance(created, (int, float)):
                    try:
                        posted_date = datetime.fromtimestamp(
                            float(created), timezone.utc
                        ).date().isoformat()
                    except (ValueError, OSError, OverflowError):
                        posted_date = None
                else:
                    posted_date = iso_date(created)

                yield {
                    "company": clean(job.get("company_name") or ""),
                    "title": clean(job.get("title") or ""),
                    "url": job.get("url") or "",
                    "location": clean(job.get("location") or ("Remote" if job.get("remote") else "")),
                    "posted_date": posted_date,
                    "description": clean(
                        BeautifulSoup(job.get("description") or "", "html.parser").get_text(" ")
                    ),
                }
        except Exception as e:
            print("Arbeitnow error:", e)
            break


def themuse():
    """The Muse public feed, used only to discover candidate postings."""
    for page in range(1, 5):
        try:
            r = SESSION.get(
                "https://www.themuse.com/api/public/jobs",
                params={"page": page},
                timeout=25,
            )
            r.raise_for_status()
            payload = r.json() or {}
            jobs = payload.get("results") or []
            if not jobs:
                break

            for job in jobs:
                company = job.get("company") or {}
                locations = job.get("locations") or []
                location_text = "; ".join(
                    clean(item.get("name") or "")
                    for item in locations
                    if isinstance(item, dict) and clean(item.get("name") or "")
                )
                refs = job.get("refs") or {}
                yield {
                    "company": clean(company.get("name") if isinstance(company, dict) else company),
                    "title": clean(job.get("name") or ""),
                    "url": refs.get("landing_page") or job.get("url") or "",
                    "location": location_text,
                    "posted_date": iso_date(job.get("publication_date")),
                    "description": clean(
                        BeautifulSoup(job.get("contents") or "", "html.parser").get_text(" ")
                    ),
                }
        except Exception as e:
            print("TheMuse error:", e)
            break

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



def existing_discovery_sources():
    """Return prior discovery rows so successful ATS boards become self-learning sources."""
    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "company,job_url,source_type",
            "user_id": f"eq.{USER_ID}",
            "limit": "10000",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json() or []


def _learned_board_targets(rows):
    targets = {
        "greenhouse": set(),
        "lever": set(),
        "ashby": set(),
        "smartrecruiters": set(),
    }
    company_hints = set()

    for row in rows:
        company = clean(row.get("company") or "")
        url = row.get("job_url") or ""
        if company:
            company_hints.add(company)
        if not url:
            continue

        parsed = urllib.parse.urlsplit(url)
        host = parsed.netloc.lower().split(":")[0]
        parts = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]

        if host in {"job-boards.greenhouse.io", "boards.greenhouse.io"} and parts:
            targets["greenhouse"].add((parts[0], company))
        elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and parts:
            targets["lever"].add((parts[0], company))
        elif host == "jobs.ashbyhq.com" and parts:
            targets["ashby"].add((parts[0], company))
        elif host == "jobs.smartrecruiters.com" and parts:
            targets["smartrecruiters"].add((parts[0], company))

    return targets, company_hints


def _direct_candidate(company, title, url, location, posted_date, description, source_name):
    return {
        "company": clean(company),
        "title": clean(title),
        "url": canonical_url(url),
        "location": clean(location),
        "posted_date": iso_date(posted_date),
        "description": clean(description),
        "candidate_source": source_name,
    }


def scan_greenhouse_board(board, company, cfg):
    out = []
    try:
        url = "https://boards-api.greenhouse.io/v1/boards/" + urllib.parse.quote(board, safe="") + "/jobs"
        r = SESSION.get(
            url,
            params={"content": "true", "pay_transparency": "true"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return out
        for job in (r.json() or {}).get("jobs", []):
            title = clean(job.get("title") or "")
            if not title_matches_config(title, cfg):
                continue
            loc = job.get("location") or {}
            location = clean(loc.get("name") if isinstance(loc, dict) else loc)
            direct_url = job.get("absolute_url") or ""
            if not direct_url:
                continue
            description = clean(BeautifulSoup(job.get("content") or "", "html.parser").get_text(" "))
            out.append(_direct_candidate(
                company or board, title, direct_url, location,
                job.get("updated_at"), description, "direct-greenhouse-scan"
            ))
    except Exception as e:
        print("Greenhouse direct-scan error:", board, e)
    return out


def scan_lever_site(site, company, cfg):
    out = []
    try:
        r = SESSION.get(
            "https://api.lever.co/v0/postings/" + urllib.parse.quote(site, safe=""),
            params={"mode": "json"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return out
        postings = r.json()
        if not isinstance(postings, list):
            return out
        for job in postings:
            title = clean(job.get("text") or "")
            if not title_matches_config(title, cfg):
                continue
            posting_id = str(job.get("id") or "").strip()
            if not posting_id:
                continue
            location = clean(((job.get("categories") or {}).get("location") or ""))
            description = clean(job.get("descriptionPlain") or job.get("description") or "")
            direct_url = f"https://jobs.lever.co/{site}/{posting_id}"
            created = job.get("createdAt")
            posted = None
            if isinstance(created, (int, float)):
                try:
                    posted = datetime.fromtimestamp(float(created) / 1000.0, timezone.utc).date().isoformat()
                except (ValueError, OSError, OverflowError):
                    posted = None
            else:
                posted = iso_date(created)
            out.append(_direct_candidate(
                company or site, title, direct_url, location,
                posted, description, "direct-lever-scan"
            ))
    except Exception as e:
        print("Lever direct-scan error:", site, e)
    return out


def scan_ashby_board(board, company, cfg):
    out = []
    try:
        r = SESSION.get(
            "https://api.ashbyhq.com/posting-api/job-board/" + urllib.parse.quote(board, safe=""),
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return out
        for job in (r.json() or {}).get("jobs", []):
            title = clean(job.get("title") or "")
            if not title_matches_config(title, cfg):
                continue
            direct_url = job.get("jobUrl") or job.get("applyUrl") or ""
            if not direct_url:
                continue
            description = clean(
                BeautifulSoup(job.get("descriptionHtml") or "", "html.parser").get_text(" ")
            )
            out.append(_direct_candidate(
                company or board, title, direct_url,
                job.get("location") or "", job.get("publishedAt"),
                description, "direct-ashby-scan"
            ))
    except Exception as e:
        print("Ashby direct-scan error:", board, e)
    return out


def scan_smartrecruiters_company(company_id, company, cfg):
    out = []
    try:
        r = SESSION.get(
            f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(company_id, safe='')}/postings",
            params={"limit": 100},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return out
        for posting in (r.json() or {}).get("content", []):
            title = clean(posting.get("name") or "")
            if not title_matches_config(title, cfg):
                continue
            pid = str(posting.get("id") or "").strip()
            if not pid:
                continue
            detail_url = f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(company_id, safe='')}/postings/{urllib.parse.quote(pid, safe='')}"
            detail = posting
            try:
                dr = SESSION.get(detail_url, timeout=REQUEST_TIMEOUT)
                if dr.status_code == 200:
                    detail = dr.json() or posting
            except Exception:
                pass
            loc = detail.get("location") or posting.get("location") or {}
            if isinstance(loc, dict):
                location = clean(", ".join(str(loc.get(k) or "") for k in ("city", "region", "country") if loc.get(k)))
            else:
                location = clean(loc)
            sections = detail.get("jobAd") or {}
            description_parts = []
            if isinstance(sections, dict):
                for value in sections.values():
                    if isinstance(value, dict):
                        value = value.get("text") or value.get("title") or ""
                    if value:
                        description_parts.append(str(value))
            description = clean(BeautifulSoup(" ".join(description_parts), "html.parser").get_text(" "))
            direct_url = f"https://jobs.smartrecruiters.com/{company_id}/{pid}"
            out.append(_direct_candidate(
                company or company_id, title, direct_url, location,
                detail.get("releasedDate") or posting.get("releasedDate"),
                description, "direct-smartrecruiters-scan"
            ))
    except Exception as e:
        print("SmartRecruiters direct-scan error:", company_id, e)
    return out


def direct_ats_harvest(cfg, candidate_companies=None):
    """Harvest new roles directly from ATS boards learned from prior successes.

    This is deliberately self-learning rather than a manually maintained company watchlist.
    It also probes a small number of likely board slugs for companies already seen in the
    current public feeds, which lets a single feed hit unlock other fresh jobs at that employer.
    """
    rows = existing_discovery_sources()
    targets, known_companies = _learned_board_targets(rows)

    for company in candidate_companies or []:
        if company:
            known_companies.add(clean(company))

    harvested = []
    seen_targets = set()

    for source_name, items in targets.items():
        for board_id, company in sorted(items):
            key = (source_name, board_id.lower())
            if key in seen_targets:
                continue
            seen_targets.add(key)
            if source_name == "greenhouse":
                harvested.extend(scan_greenhouse_board(board_id, company, cfg))
            elif source_name == "lever":
                harvested.extend(scan_lever_site(board_id, company, cfg))
            elif source_name == "ashby":
                harvested.extend(scan_ashby_board(board_id, company, cfg))
            elif source_name == "smartrecruiters":
                harvested.extend(scan_smartrecruiters_company(board_id, company, cfg))

    # Probe likely board slugs for only a capped set of known/current companies.
    # Successful probes add full matching job inventories from that board.
    for company in sorted(known_companies)[:60]:
        if company_is_excluded(company, cfg):
            continue
        variants = company_slug_variants(company)[:4]
        found_for_company = False
        for board in variants:
            for source_name, scanner in (
                ("greenhouse", scan_greenhouse_board),
                ("lever", scan_lever_site),
                ("ashby", scan_ashby_board),
                ("smartrecruiters", scan_smartrecruiters_company),
            ):
                key = (source_name, board.lower())
                if key in seen_targets:
                    continue
                seen_targets.add(key)
                batch = scanner(board, company, cfg)
                if batch:
                    harvested.extend(batch)
                    found_for_company = True
            if found_for_company:
                break

    print("Direct ATS harvest candidates:", len(harvested))
    return harvested

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


def sync_linked_tracker_compensation(tracker_job_id, salary_min, salary_max):
    """Repair a linked Application Tracker row when its saved salary is blank/invalid.

    Discovery rows that were marked applied before compensation extraction existed may
    already have copied malformed values (for example 208 instead of 208000) into the
    jobs table. Only blank/invalid tracker values are replaced so valid manual edits are
    left alone.
    """
    if not tracker_job_id or salary_min is None or salary_max is None:
        return False

    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/jobs",
        headers=supabase_headers(),
        params={
            "select": "id,salary_min,salary_max",
            "id": f"eq.{tracker_job_id}",
            "user_id": f"eq.{USER_ID}",
            "limit": "1",
        },
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json() or []
    if not rows:
        return False

    current = rows[0]
    if not (
        salary_missing_or_invalid(current.get("salary_min"))
        or salary_missing_or_invalid(current.get("salary_max"))
    ):
        return False

    pr = SESSION.patch(
        f"{SUPABASE_URL}/rest/v1/jobs",
        headers={
            **supabase_headers(),
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        params={
            "id": f"eq.{tracker_job_id}",
            "user_id": f"eq.{USER_ID}",
        },
        data=json.dumps({
            "salary_min": salary_min,
            "salary_max": salary_max,
        }),
        timeout=30,
    )
    if pr.status_code >= 300:
        raise RuntimeError(
            f"Supabase linked tracker compensation sync failed {pr.status_code}: {pr.text}"
        )
    return True


def backfill_existing_matches():
    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "id,company,role,job_url,description,location,match_score,match_summary,salary_min,salary_max,tracker_job_id",
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
        needs_salary_refresh = (
            salary_missing_or_invalid(row.get("salary_min"))
            or salary_missing_or_invalid(row.get("salary_max"))
        )
        refreshed = None
        if needs_salary_refresh and row.get("job_url"):
            refreshed = fetch_direct_job_details(
                row.get("job_url") or "",
                row.get("role") or "",
                row.get("company") or "",
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

        salary_min = (refreshed or {}).get("salary_min")
        salary_max = (refreshed or {}).get("salary_max")
        if salary_min is None or salary_max is None:
            parsed_min, parsed_max = extract_base_compensation(description)
            if salary_min is None:
                salary_min = parsed_min
            if salary_max is None:
                salary_max = parsed_max

        # Repair the legacy bad unit that briefly stored $208k-$261k as 208-261.
        # This is only used when live/parsed compensation did not provide a valid
        # annual range, and only for a plausible shorthand pair.
        if salary_min is None or salary_max is None:
            try:
                old_min = float(row.get("salary_min"))
                old_max = float(row.get("salary_max"))
            except (TypeError, ValueError):
                old_min = old_max = 0
            if 20 <= old_min < 1000 and 20 <= old_max < 1000:
                salary_min = int(round(old_min * 1000))
                salary_max = int(round(old_max * 1000))
                if salary_min > salary_max:
                    salary_min, salary_max = salary_max, salary_min
                print(
                    "REPAIRED LEGACY COMP UNITS:",
                    row.get("company") or "",
                    "|",
                    row.get("role") or "",
                    "|",
                    f"${salary_min:,} - ${salary_max:,}",
                )

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

            if sync_linked_tracker_compensation(
                row.get("tracker_job_id"),
                salary_min,
                salary_max,
            ):
                print(
                    "SYNCED TRACKER COMP:",
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



def purge_discovery_noise(min_match_score=80, max_age_days=30):
    """Remove discovery rows that should no longer be surfaced.

    - Rows below the minimum fit threshold are removed.
    - Rows older than max_age_days are removed.
    - Rows explicitly soft-deleted by the user are preserved as tombstones so
      the same source_key cannot be rediscovered on the next sync.
    """
    r = SESSION.get(
        f"{SUPABASE_URL}/rest/v1/discovered_jobs",
        headers=supabase_headers(),
        params={
            "select": "id,company,role,job_url,description,location,posted_date,first_seen_at,match_score,decision,pass_reason",
            "user_id": f"eq.{USER_ID}",
            "limit": "10000",
        },
        timeout=30,
    )
    r.raise_for_status()

    purge_ids = []
    low_match = 0
    stale = 0
    clearance_blocked = 0
    geography_blocked = 0
    title_blocked = 0

    for row in r.json() or []:
        job_id = row.get("id")
        if not job_id:
            continue

        # Keep user-deleted rows as hidden tombstones so their source_key remains
        # in Supabase and the posting does not come back on the next harvest.
        if (
            str(row.get("decision") or "").lower() == "passed"
            and str(row.get("pass_reason") or "") == "Deleted by user"
        ):
            continue

        # Existing Discovery rows may contain stale/incomplete ATS metadata from
        # an earlier scan. Re-fetch the direct posting before re-validating
        # clearance, location, and work arrangement.
        fresh_role = row.get("role") or ""
        fresh_description = row.get("description") or ""
        fresh_location = row.get("location") or ""

        if row.get("job_url"):
            refreshed = fetch_direct_job_details(
                row.get("job_url") or "",
                fresh_role,
                row.get("company") or "",
            )
            if refreshed:
                fresh_role = refreshed.get("title") or fresh_role
                fresh_description = refreshed.get("description") or fresh_description
                fresh_location = refreshed.get("location") or fresh_location

        score = row.get("match_score")
        try:
            score_num = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_num = None

        should_purge = False
        if score_num is not None and score_num < float(min_match_score):
            should_purge = True
            low_match += 1

        if not title_matches_config(fresh_role, load_config()):
            if not should_purge:
                title_blocked += 1
            should_purge = True

        if requires_active_ts(fresh_role, fresh_description):
            if not should_purge:
                clearance_blocked += 1
            should_purge = True

        if not location_matches(
            fresh_location,
            load_config(),
            fresh_description,
        ):
            if not should_purge:
                geography_blocked += 1
            should_purge = True

        if is_too_old(row.get("posted_date"), int(max_age_days)):
            if not should_purge:
                stale += 1
            should_purge = True

        if should_purge:
            purge_ids.append(job_id)

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
            raise RuntimeError(
                f"Supabase discovery cleanup failed {dr.status_code}: {dr.text}"
            )

    if purge_ids:
        print(
            f"Discovery cleanup removed {len(purge_ids)} rows "
            f"({low_match} below {min_match_score}% match; "
            f"{title_blocked} title-mismatch; "
            f"{clearance_blocked} clearance-blocked; "
            f"{geography_blocked} not US-remote or DMV remote/hybrid; "
            f"{stale} stale > {max_age_days} days)."
        )
    else:
        print(
            f"Discovery cleanup: nothing to remove "
            f"(minimum match {min_match_score}%, max age {max_age_days} days)."
        )


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
    min_match_score = int(cfg.get("min_match_score", 80))

    # Keep Discovery intentionally selective on every sync.
    purge_discovery_noise(min_match_score, max_age_days)

    purge_existing_active_ts()
    backfill_existing_matches()
    known = existing_keys()

    raw = []

    source_batches = [
        ("Himalayas", list(himalayas(cfg))),
        ("WeWorkRemotely", list(weworkremotely(cfg))),
        ("Jobicy", list(jobicy(cfg))),
        ("Remotive", list(remotive())),
        ("RemoteOK", list(remoteok())),
        ("Arbeitnow", list(arbeitnow())),
        ("TheMuse", list(themuse())),
    ]

    for source_name, batch in source_batches:
        print(f"{source_name} candidates fetched: {len(batch)}")
        for job in batch:
            job.setdefault("candidate_source", source_name.lower())
        raw.extend(batch)

    # Search-engine HTML/RSS scraping is intentionally NOT in the critical path.
    # GitHub-hosted runners are unreliable for Bing/DDG. Himalayas gives us
    # deterministic title-first discovery without an API key; provider pages are
    # still resolved to direct employer/ATS URLs before anything can be inserted.

    # Harvest ATS boards learned from previous successful resolutions and companies
    # appearing in the current feeds so each scheduled run can also catch newly
    # posted jobs directly from Greenhouse/Lever/Ashby/SmartRecruiters.
    current_companies = {
        clean(job.get("company") or "")
        for job in raw
        if clean(job.get("company") or "")
    }
    raw.extend(direct_ats_harvest(cfg, current_companies))

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
            job.get("description") or "",
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
            norm(job.get("location") or ""),
            canonical_url(job.get("url") or ""),
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

        resolved_title = ats.get("title") or title
        resolved_description = (
            ats.get("description")
            or job.get("description")
            or ""
        )

        # Revalidate geography using the enriched direct posting rather than
        # trusting an upstream "remote" hint.
        if not location_matches(
            resolved_location,
            cfg,
            resolved_description,
        ):
            location_rejected += 1

            print(
                "REJECTED ATS LOCATION:",
                company,
                "|",
                resolved_title,
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

        if match_score < min_match_score:
            print(
                "EXCLUDED LOW MATCH:",
                company,
                "|",
                resolved_title,
                "|",
                f"{match_score}% < {min_match_score}%",
            )
            continue

        salary_min = ats.get("salary_min")
        salary_max = ats.get("salary_max")
        if salary_min is None or salary_max is None:
            parsed_min, parsed_max = extract_base_compensation(resolved_description)
            if salary_min is None:
                salary_min = parsed_min
            if salary_max is None:
                salary_max = parsed_max

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
            "work_arrangement": infer_work_arrangement(
                resolved_location,
                resolved_description,
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
