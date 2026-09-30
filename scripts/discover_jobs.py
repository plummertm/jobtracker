#!/usr/bin/env python3

import os
import re
import json
import html
import hashlib
import urllib.parse

from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USER_ID = os.environ["SUPABASE_USER_ID"]

ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

CONFIG_PATH = os.path.join(
    ROOT,
    "config",
    "sources.json"
)

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/153.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": (
            "application/json,"
            "text/html,"
            "application/xhtml+xml;q=0.9,"
            "*/*;q=0.8"
        ),
    }
)

REQUEST_TIMEOUT = 18


DIRECT_ATS_HOSTS = {
    "jobs.ashbyhq.com":
        "ashby",

    "jobs.lever.co":
        "lever",

    "jobs.eu.lever.co":
        "lever",

    "job-boards.greenhouse.io":
        "greenhouse",

    "boards.greenhouse.io":
        "greenhouse",

    "jobs.smartrecruiters.com":
        "smartrecruiters",
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


def load_config():

    with open(
        CONFIG_PATH,
        "r",
        encoding="utf-8",
    ) as f:

        return json.load(f)


def clean(value):

    return re.sub(
        r"\s+",
        " ",
        html.unescape(
            str(value or "")
        ),
    ).strip()


def norm(value):

    value = clean(value).lower()

    value = value.replace(
        "&",
        " and "
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def host_of(url):

    try:

        return (
            urllib.parse
            .urlparse(url)
            .netloc
            .lower()
            .split(":")[0]
        )

    except Exception:

        return ""


def canonical_url(url):

    try:

        p = urllib.parse.urlsplit(
            url
        )

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

    return DIRECT_ATS_HOSTS.get(
        host_of(url),
        "direct",
    )


def is_direct_ats(url):

    host = host_of(url)

    if any(
        part in host
        for part
        in BLOCKED_HOST_PARTS
    ):

        return False

    return (
        host in
        DIRECT_ATS_HOSTS
    )


def source_key(url):

    return hashlib.sha256(
        canonical_url(url)
        .encode("utf-8")
    ).hexdigest()[:48]


def iso_date(value):

    if not value:
        return None

    text = str(value)

    match = re.search(
        r"\d{4}-\d{2}-\d{2}",
        text,
    )

    if match:

        return match.group(0)

    return None


def parse_iso_date(value):

    value = iso_date(value)

    if not value:
        return None

    try:

        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()

    except ValueError:

        return None


def is_too_old(
    posted_date,
    max_days,
):

    if not posted_date:
        return False

    parsed = parse_iso_date(
        posted_date
    )

    if not parsed:
        return False

    cutoff = (
        datetime.now(
            timezone.utc
        ).date()
        -
        timedelta(
            days=max_days
        )
    )

    return (
        parsed <
        cutoff
    )


def company_is_excluded(
    company,
    cfg,
):

    normalized =
        norm(company)

    for excluded in cfg.get(
        "exclude_companies",
        [],
    ):

        target =
            norm(excluded)

        if not target:
            continue

        if (
            normalized == target
            or target in normalized
        ):

            return True

    return False


def title_matches_config(
    title,
    cfg,
):

    title_n =
        norm(title)

    if not title_n:
        return False

    for excluded in cfg.get(
        "exclude_titles",
        [],
    ):

        if (
            norm(excluded)
            in title_n
        ):

            return False

    for wanted in cfg.get(
        "titles",
        [],
    ):

        target =
            norm(wanted)

        if (
            target in title_n
            or title_n in target
        ):

            return True

    return False


def location_matches(
    location,
    cfg,
):

    if not cfg.get(
        "us_only",
        True,
    ):

        return True

    location_n =
        norm(location)

    if not location_n:
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

    if any(
        value in location_n
        for value
        in positives
    ):

        return True

    if any(
        value in location_n
        for value
        in negatives
    ):

        return False

    return True


def title_similarity(
    candidate,
    actual,
):

    a =
        norm(candidate)

    b =
        norm(actual)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if (
        a in b
        or b in a
    ):

        return 0.93

    a_tokens =
        set(a.split())

    b_tokens =
        set(b.split())

    token_score = 0.0

    if (
        a_tokens
        and b_tokens
    ):

        token_score = (
            len(
                a_tokens &
                b_tokens
            )
            /
            len(
                a_tokens |
                b_tokens
            )
        )

    sequence_score =
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio()

    return max(
        token_score,
        sequence_score,
    )


def best_match(
    candidate_title,
    postings,
    title_getter,
    threshold=0.66,
):

    matches = []

    for posting in postings:

        score =
            title_similarity(
                candidate_title,
                title_getter(
                    posting
                ),
            )

        if (
            score >=
            threshold
        ):

            matches.append(
                (
                    score,
                    posting,
                )
            )

    if not matches:
        return None

    matches.sort(
        key=lambda item:
            item[0],
        reverse=True,
    )

    return matches[0]


def company_slug_variants(
    company
):

    base_words =
        norm(company)
        .split()

    while (
        base_words
        and
        base_words[-1]
        in CORPORATE_SUFFIXES
    ):

        base_words.pop()

    if not base_words:
        return []

    base =
        " ".join(
            base_words
        )

    candidates = [
        base,
        "".join(
            base_words
        ),
        "-".join(
            base_words
        ),
        "_".join(
            base_words
        ),
    ]

    aliases = {
        "trm labs":
            [
                "trm-labs",
                "trmlabs",
            ],

        "gitlab":
            [
                "gitlab"
            ],

        "databricks":
            [
                "databricks"
            ],

        "zscaler":
            [
                "zscaler"
            ],

        "tenable":
            [
                "tenable"
            ],

        "canonical":
            [
                "canonical"
            ],

        "ashby":
            [
                "ashby"
            ],

        "webflow":
            [
                "webflow"
            ],

        "deepgram":
            [
                "deepgram"
            ],

        "infisical":
            [
                "infisical"
            ],

        "amplitude":
            [
                "amplitude"
            ],

        "revenuecat":
            [
                "revenuecat"
            ],

        "axiom":
            [
                "axiom"
            ],

        "extrahop":
            [
                "extrahop"
            ],

        "commvault":
            [
                "commvault"
            ],

        "aviatrix":
            [
                "aviatrix"
            ],

        "upguard":
            [
                "upguard"
            ],

        "semperis":
            [
                "semperis"
            ],
    }

    candidates.extend(
        aliases.get(
            base,
            [],
        )
    )

    output = []
    seen = set()

    for candidate in candidates:

        cleaned = re.sub(
            r"[^A-Za-z0-9_-]",
            "",
            candidate,
        )

        if not cleaned:
            continue

        key =
            cleaned.lower()

        if (
            key not in seen
        ):

            seen.add(key)
            output.append(
                cleaned
            )

    return output[:10]


def direct_from_provider_page(
    url
):

    if not url:
        return None

    try:

        r = SESSION.get(
            url,
            timeout=
                REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        r.raise_for_status()

        final_url =
            canonical_url(
                r.url
            )

        if is_direct_ats(
            final_url
        ):

            return final_url

        soup =
            BeautifulSoup(
                r.text,
                "html.parser",
            )

        for link in soup.find_all(
            "a",
            href=True,
        ):

            candidate =
                canonical_url(
                    urllib.parse.urljoin(
                        final_url,
                        link.get(
                            "href"
                        ),
                    )
                )

            if is_direct_ats(
                candidate
            ):

                return candidate

    except Exception:

        pass

    return None


def ashby_resolve(
    company,
    title,
):

    for board in company_slug_variants(
        company
    ):

        url = (
            "https://api.ashbyhq.com/"
            "posting-api/job-board/"
            +
            urllib.parse.quote(
                board,
                safe="",
            )
        )

        try:

            r = SESSION.get(
                url,
                timeout=
                    REQUEST_TIMEOUT,
            )

            if (
                r.status_code !=
                200
            ):
                continue

            jobs =
                r.json().get(
                    "jobs",
                    [],
                )

            match =
                best_match(
                    title,
                    jobs,
                    lambda j:
                        j.get(
                            "title"
                        )
                        or "",
                )

            if not match:
                continue

            score, job =
                match

            job_url =
                job.get(
                    "jobUrl"
                ) or job.get(
                    "applyUrl"
                )

            if (
                not job_url
                or
                not is_direct_ats(
                    job_url
                )
            ):
                continue

            return {
                "url":
                    canonical_url(
                        job_url
                    ),

                "title":
                    job.get(
                        "title"
                    )
                    or title,

                "location":
                    job.get(
                        "location"
                    )
                    or "",

                "posted_date":
                    iso_date(
                        job.get(
                            "publishedAt"
                        )
                    ),

                "description":
                    clean(
                        BeautifulSoup(
                            job.get(
                                "descriptionHtml"
                            )
                            or "",
                            "html.parser",
                        )
                        .get_text(" ")
                    ),

                "source_type":
                    "ashby",

                "match_score":
                    score,
            }

        except Exception:

            continue

    return None


def lever_resolve(
    company,
    title,
):

    for site in company_slug_variants(
        company
    ):

        url = (
            "https://api.lever.co/"
            "v0/postings/"
            +
            urllib.parse.quote(
                site,
                safe="",
            )
        )

        try:

            r = SESSION.get(
                url,
                params={
                    "mode":
                        "json",
                },
                timeout=
                    REQUEST_TIMEOUT,
            )

            if (
                r.status_code !=
                200
            ):
                continue

            postings =
                r.json()

            if not isinstance(
                postings,
                list,
            ):
                continue

            match =
                best_match(
                    title,
                    postings,
                    lambda j:
                        j.get(
                            "text"
                        )
                        or "",
                )

            if not match:
                continue

            score, job =
                match

            posting_id =
                str(
                    job.get(
                        "id"
                    )
                    or ""
                )

            if not posting_id:
                continue

            direct_url = (
                "https://jobs.lever.co/"
                f"{site}/"
                f"{posting_id}"
            )

            location =
                (
                    job.get(
                        "categories"
                    )
                    or {}
                ).get(
                    "location",
                    "",
                )

            return {
                "url":
                    canonical_url(
                        direct_url
                    ),

                "title":
                    job.get(
                        "text"
                    )
                    or title,

                "location":
                    location,

                "posted_date":
                    iso_date(
                        job.get(
                            "createdAt"
                        )
                    ),

                "description":
                    clean(
                        job.get(
                            "descriptionPlain"
                        )
                        or
                        job.get(
                            "description"
                        )
                        or ""
                    ),

                "source_type":
                    "lever",

                "match_score":
                    score,
            }

        except Exception:

            continue

    return None


def greenhouse_resolve(
    company,
    title,
):

    for board in company_slug_variants(
        company
    ):

        url = (
            "https://boards-api."
            "greenhouse.io/"
            "v1/boards/"
            +
            urllib.parse.quote(
                board,
                safe="",
            )
            +
            "/jobs"
        )

        try:

            r = SESSION.get(
                url,
                params={
                    "content":
                        "true",
                },
                timeout=
                    REQUEST_TIMEOUT,
            )

            if (
                r.status_code !=
                200
            ):
                continue

            postings =
                r.json().get(
                    "jobs",
                    [],
                )

            match =
                best_match(
                    title,
                    postings,
                    lambda j:
                        j.get(
                            "title"
                        )
                        or "",
                )

            if not match:
                continue

            score, job =
                match

            direct_url =
                job.get(
                    "absolute_url"
                )
                or ""

            if not is_direct_ats(
                direct_url
            ):

                continue

            location =
                (
                    job.get(
                        "location"
                    )
                    or {}
                ).get(
                    "name",
                    "",
                )

            return {
                "url":
                    canonical_url(
                        direct_url
                    ),

                "title":
                    job.get(
                        "title"
                    )
                    or title,

                "location":
                    location,

                "posted_date":
                    iso_date(
                        job.get(
                            "updated_at"
                        )
                    ),

                "description":
                    clean(
                        BeautifulSoup(
                            job.get(
                                "content"
                            )
                            or "",
                            "html.parser",
                        )
                        .get_text(" ")
                    ),

                "source_type":
                    "greenhouse",

                "match_score":
                    score,
            }

        except Exception:

            continue

    return None


def smartrecruiters_resolve(
    company,
    title,
):

    for company_id in company_slug_variants(
        company
    ):

        try:

            r = SESSION.get(
                (
                    "https://api."
                    "smartrecruiters.com/"
                    "v1/companies/"
                    f"{company_id}/"
                    "postings"
                ),
                params={
                    "limit":
                        100,
                },
                timeout=
                    REQUEST_TIMEOUT,
            )

            if (
                r.status_code !=
                200
            ):
                continue

            postings =
                r.json().get(
                    "content",
                    [],
                )

            match =
                best_match(
                    title,
                    postings,
                    lambda j:
                        j.get(
                            "name"
                        )
                        or "",
                )

            if not match:
                continue

            score, job =
                match

            posting_id =
                str(
                    job.get(
                        "id"
                    )
                    or ""
                )

            if not posting_id:
                continue

            job_title =
                job.get(
                    "name"
                )
                or title

            title_slug =
                re.sub(
                    r"[^a-z0-9]+",
                    "-",
                    norm(
                        job_title
                    ),
                ).strip("-")

            direct_url = (
                "https://jobs."
                "smartrecruiters.com/"
                f"{company_id}/"
                f"{posting_id}-"
                f"{title_slug}"
            )

            loc =
                job.get(
                    "location"
                )
                or {}

            location =
                ", ".join(
                    value
                    for value
                    in [
                        loc.get(
                            "city"
                        ),
                        loc.get(
                            "region"
                        ),
                        loc.get(
                            "country"
                        ),
                    ]
                    if value
                )

            return {
                "url":
                    canonical_url(
                        direct_url
                    ),

                "title":
                    job_title,

                "location":
                    location,

                "posted_date":
                    iso_date(
                        job.get(
                            "releasedDate"
                        )
                    ),

                "description":
                    "",

                "source_type":
                    "smartrecruiters",

                "match_score":
                    score,
            }

        except Exception:

            continue

    return None


def resolve_direct_ats(
    company,
    title,
    provider_url,
):

    direct =
        direct_from_provider_page(
            provider_url
        )

    if direct:

        return {
            "url":
                direct,

            "title":
                title,

            "location":
                "",

            "posted_date":
                None,

            "description":
                "",

            "source_type":
                source_type(
                    direct
                ),

            "match_score":
                1.0,
        }


    for resolver in [
        ashby_resolve,
        lever_resolve,
        greenhouse_resolve,
        smartrecruiters_resolve,
    ]:

        result =
            resolver(
                company,
                title,
            )

        if result:

            return result

    return None


def jobicy(cfg):

    seen = set()

    for title in cfg[
        "titles"
    ]:

        try:

            r = SESSION.get(
                (
                    "https://jobicy.com/"
                    "api/v2/"
                    "remote-jobs"
                ),
                params={
                    "count":
                        200,

                    "geo":
                        "usa",

                    "tag":
                        title,
                },
                timeout=25,
            )

            r.raise_for_status()

            for job in (
                r.json()
                .get(
                    "jobs",
                    [],
                )
            ):

                jid =
                    str(
                        job.get(
                            "id"
                        )
                        or ""
                    )

                if (
                    jid
                    and
                    jid in seen
                ):
                    continue

                if jid:
                    seen.add(
                        jid
                    )

                yield {
                    "company":
                        clean(
                            job.get(
                                "companyName"
                            )
                            or ""
                        ),

                    "title":
                        clean(
                            job.get(
                                "jobTitle"
                            )
                            or ""
                        ),

                    "url":
                        job.get(
                            "url"
                        )
                        or "",

                    "location":
                        clean(
                            job.get(
                                "jobGeo"
                            )
                            or "Remote"
                        ),

                    "posted_date":
                        iso_date(
                            job.get(
                                "pubDate"
                            )
                        ),

                    "description":
                        clean(
                            BeautifulSoup(
                                job.get(
                                    "jobDescription"
                                )
                                or "",
                                "html.parser",
                            )
                            .get_text(" ")
                        ),
                }

        except Exception as e:

            print(
                "Jobicy error:",
                e,
            )


def remotive():

    try:

        r = SESSION.get(
            (
                "https://remotive.com/"
                "api/remote-jobs"
            ),
            timeout=25,
        )

        r.raise_for_status()

        for job in (
            r.json()
            .get(
                "jobs",
                [],
            )
        ):

            yield {
                "company":
                    clean(
                        job.get(
                            "company_name"
                        )
                        or ""
                    ),

                "title":
                    clean(
                        job.get(
                            "title"
                        )
                        or ""
                    ),

                "url":
                    job.get(
                        "url"
                    )
                    or "",

                "location":
                    clean(
                        job.get(
                            "candidate_required_location"
                        )
                        or "Remote"
                    ),

                "posted_date":
                    iso_date(
                        job.get(
                            "publication_date"
                        )
                    ),

                "description":
                    clean(
                        BeautifulSoup(
                            job.get(
                                "description"
                            )
                            or "",
                            "html.parser",
                        )
                        .get_text(" ")
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

        data =
            r.json()

        if (
            isinstance(
                data,
                list,
            )
            and data
            and not data[0].get(
                "position"
            )
        ):

            data =
                data[1:]

        for job in (
            data
            if isinstance(
                data,
                list,
            )
            else []
        ):

            yield {
                "company":
                    clean(
                        job.get(
                            "company"
                        )
                        or ""
                    ),

                "title":
                    clean(
                        job.get(
                            "position"
                        )
                        or ""
                    ),

                "url":
                    job.get(
                        "url"
                    )
                    or
                    job.get(
                        "apply_url"
                    )
                    or "",

                "location":
                    clean(
                        job.get(
                            "location"
                        )
                        or "Remote"
                    ),

                "posted_date":
                    iso_date(
                        job.get(
                            "date"
                        )
                    ),

                "description":
                    clean(
                        BeautifulSoup(
                            job.get(
                                "description"
                            )
                            or "",
                            "html.parser",
                        )
                        .get_text(" ")
                    ),
            }

    except Exception as e:

        print(
            "RemoteOK error:",
            e,
        )


def supabase_headers():

    return {
        "apikey":
            SUPABASE_KEY,

        "Authorization":
            (
                "Bearer "
                f"{SUPABASE_KEY}"
            ),
    }


def existing_keys():

    r = SESSION.get(
        (
            f"{SUPABASE_URL}"
            "/rest/v1/"
            "discovered_jobs"
        ),
        headers=
            supabase_headers(),
        params={
            "select":
                "source_key",

            "user_id":
                f"eq.{USER_ID}",

            "source_key":
                "not.is.null",

            "limit":
                "10000",
        },
        timeout=30,
    )

    r.raise_for_status()

    return {
        row[
            "source_key"
        ]
        for row
        in r.json()
        if row.get(
            "source_key"
        )
    }


def insert_rows(
    rows
):

    if not rows:
        return

    headers = {
        **supabase_headers(),

        "Content-Type":
            "application/json",

        "Prefer":
            (
                "return=minimal,"
                "resolution=ignore-duplicates"
            ),
    }

    for i in range(
        0,
        len(rows),
        100,
    ):

        batch =
            rows[
                i:
                i + 100
            ]

        r = SESSION.post(
            (
                f"{SUPABASE_URL}"
                "/rest/v1/"
                "discovered_jobs"
            ),
            headers=headers,
            data=json.dumps(
                batch
            ),
            timeout=30,
        )

        if (
            r.status_code >=
            300
        ):

            raise RuntimeError(
                "Supabase insert failed "
                f"{r.status_code}: "
                f"{r.text}"
            )


def main():

    cfg =
        load_config()

    max_age_days =
        int(
            cfg.get(
                "max_post_age_days",
                30,
            )
        )

    known =
        existing_keys()

    raw = []

    raw.extend(
        list(
            jobicy(
                cfg
            )
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

        if (
            not job[
                "company"
            ]
            or
            not job[
                "title"
            ]
        ):
            continue


        if company_is_excluded(
            job[
                "company"
            ],
            cfg,
        ):

            print(
                "EXCLUDED COMPANY:",
                job[
                    "company"
                ],
                "|",
                job[
                    "title"
                ],
            )

            continue


        if not title_matches_config(
            job[
                "title"
            ],
            cfg,
        ):

            continue


        if not location_matches(
            job[
                "location"
            ],
            cfg,
        ):

            continue


        if is_too_old(
            job.get(
                "posted_date"
            ),
            max_age_days,
        ):

            print(
                "STALE CANDIDATE:",
                job[
                    "company"
                ],
                "|",
                job[
                    "title"
                ],
                "|",
                job.get(
                    "posted_date"
                ),
            )

            continue


        key = (
            norm(
                job[
                    "company"
                ]
            ),

            norm(
                job[
                    "title"
                ]
            ),
        )


        if (
            key not in
            candidates
        ):

            candidates[
                key
            ] = job


    print(
        "Candidate jobs after filters:",
        len(
            candidates
        ),
    )


    rows = []
    resolved = 0
    unresolved = 0
    stale = 0
    duplicates = 0


    for job in candidates.values():

        company =
            job[
                "company"
            ]

        title =
            job[
                "title"
            ]


        ats =
            resolve_direct_ats(
                company,
                title,
                job.get(
                    "url"
                )
                or "",
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


        direct_url =
            canonical_url(
                ats[
                    "url"
                ]
            )


        if not is_direct_ats(
            direct_url
        ):

            unresolved += 1

            continue


        resolved_posted_date = (
            ats.get(
                "posted_date"
            )
            or
            job.get(
                "posted_date"
            )
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


        key =
            source_key(
                direct_url
            )


        if (
            key in known
        ):

            duplicates += 1
            continue


        resolved_title = (
            ats.get(
                "title"
            )
            or title
        )


        resolved_location = (
            ats.get(
                "location"
            )
            or
            job.get(
                "location"
            )
            or ""
        )


        resolved_description = (
            ats.get(
                "description"
            )
            or
            job.get(
                "description"
            )
            or ""
        )


        rows.append(
            {
                "user_id":
                    USER_ID,

                "company":
                    company,

                "role":
                    resolved_title,

                "job_url":
                    direct_url,

                "posted_date":
                    resolved_posted_date,

                "location":
                    resolved_location
                    or None,

                "work_arrangement":
                    (
                        "Remote"
                        if
                        "remote"
                        in norm(
                            resolved_location
                        )
                        else None
                    ),

                "source_site":
                    (
                        f"{company} Careers · "
                        f"{ats.get(
                            'source_type',
                            source_type(
                                direct_url
                            )
                        ).title()}"
                    ),

                "description":
                    resolved_description
                    or None,

                "external_job_id":
                    direct_url
                    .rstrip("/")
                    .split("/")[-1],

                "source_type":
                    ats.get(
                        "source_type",
                        source_type(
                            direct_url
                        ),
                    ),

                "source_key":
                    key,

                "decision":
                    "new",

                "first_seen_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),

                "last_seen_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            }
        )


        known.add(
            key
        )

        resolved += 1


        print(
            "RESOLVED:",
            company,
            "|",
            resolved_title,
            "|",
            resolved_posted_date,
            "->",
            direct_url,
        )


    insert_rows(
        rows
    )


    print("")
    print(
        "========== JOB DISCOVERY SUMMARY =========="
    )

    print(
        "Candidates checked:",
        len(
            candidates
        ),
    )

    print(
        "Resolved direct ATS postings:",
        resolved,
    )

    print(
        "Stale postings skipped:",
        stale,
    )

    print(
        "Already known / duplicates:",
        duplicates,
    )

    print(
        "Unresolved candidates skipped:",
        unresolved,
    )

    print(
        "Inserted new direct-company/ATS jobs:",
        len(
            rows
        ),
    )

    print(
        "==========================================="
    )


if __name__ == "__main__":
    main()
