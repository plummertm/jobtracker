#!/usr/bin/env python3
import csv
import glob
import os
import sys
from datetime import datetime, timezone

import discover_jobs as dj


def newest_csv(pattern):
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def normalize_ats_name(value):
    v = dj.norm(value or "")
    aliases = {
        "greenhouse": "greenhouse",
        "lever": "lever",
        "ashby": "ashby",
        "smartrecruiters": "smartrecruiters",
        "workday": "workday",
        "workable": "workable",
        "teamtailor": "teamtailor",
        "recruitee": "recruitee",
        "personio": "personio",
        "breezy": "breezy",
        "breezy hr": "breezy",
        "bamboohr": "bamboohr",
        "jobvite": "jobvite",
        "join": "join",
        "join com": "join",
        "rippling": "rippling",
    }
    return aliases.get(v, v or "direct")


def parse_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            yield {k: (v or "").strip() for k, v in row.items()}


def main():
    cfg = dj.load_config()
    max_age_days = int(cfg.get("max_post_age_days", 30))
    min_match_score = int(cfg.get("min_match_score", 80))

    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = newest_csv("_vendor/openjobs/output/jobs-*.csv")

    if not csv_path or not os.path.exists(csv_path):
        print("No OpenJobs harvest CSV found; nothing to import.")
        return 0

    # Keep existing rows healthy before adding fresh jobs.
    dj.purge_existing_active_ts()
    dj.backfill_existing_matches()
    dj.purge_discovery_noise(min_match_score, max_age_days)

    known = dj.existing_keys()
    rows_to_insert = []

    checked = 0
    excluded_company = 0
    title_rejected = 0
    location_rejected = 0
    stale = 0
    active_ts = 0
    low_match = 0
    duplicates = 0
    detail_fetch_failed = 0
    accepted = 0

    for row in parse_rows(csv_path):
        checked += 1

        company = row.get("company", "")
        role = row.get("role", "")
        base_location = row.get("location", "")
        posted_date = dj.iso_date(row.get("posted_date"))
        direct_url = dj.canonical_url(row.get("apply_url") or row.get("detail_url") or "")
        ats_name = normalize_ats_name(row.get("ats"))

        if not company or not role or not direct_url:
            continue

        if dj.company_is_excluded(company, cfg):
            excluded_company += 1
            continue

        if not dj.title_matches_config(role, cfg):
            title_rejected += 1
            continue

        # OpenJobs already gives direct employer/ATS destinations. We still block
        # known aggregators and classify the ATS when possible.
        if any(part in dj.host_of(direct_url) for part in dj.BLOCKED_HOST_PARTS):
            continue

        detail = dj.fetch_direct_job_details(direct_url, role, company)
        if detail:
            resolved_url = dj.canonical_url(detail.get("url") or direct_url)
            resolved_role = detail.get("title") or role
            resolved_location = detail.get("location") or base_location
            resolved_posted = detail.get("posted_date") or posted_date
            description = detail.get("description") or ""
            source_type = detail.get("source_type") or ats_name
            salary_min = detail.get("salary_min")
            salary_max = detail.get("salary_max")
        else:
            detail_fetch_failed += 1
            resolved_url = direct_url
            resolved_role = role
            resolved_location = base_location
            resolved_posted = posted_date
            description = ""
            source_type = ats_name
            salary_min = None
            salary_max = None

        if not dj.title_matches_config(resolved_role, cfg):
            title_rejected += 1
            continue

        if not dj.location_matches(resolved_location, cfg, description):
            location_rejected += 1
            print("EXCLUDED LOCATION:", company, "|", resolved_role, "|", resolved_location)
            continue

        if dj.is_too_old(resolved_posted, max_age_days):
            stale += 1
            continue

        if dj.requires_active_ts(resolved_role, description):
            active_ts += 1
            print("EXCLUDED ACTIVE TS:", company, "|", resolved_role)
            continue

        if salary_min is None or salary_max is None:
            text_min, text_max = dj.extract_base_compensation(description)
            salary_min = salary_min if salary_min is not None else text_min
            salary_max = salary_max if salary_max is not None else text_max

        match_score, match_summary = dj.calculate_match(
            resolved_role,
            description,
            resolved_location,
        )
        if match_score < min_match_score:
            low_match += 1
            continue

        if not description:
            match_summary += " Fit is provisional because the ATS export did not expose full JD text."

        key = dj.source_key(resolved_url)
        now_iso = datetime.now(timezone.utc).isoformat()

        if key in known:
            dj.update_existing_match(
                key,
                match_score,
                match_summary,
                now_iso,
                salary_min,
                salary_max,
            )
            duplicates += 1
            continue

        # Do not trust the upstream harvester's remote boolean. Use the
        # direct posting's location/JD evidence only.
        work_arrangement = dj.infer_work_arrangement(
            resolved_location,
            description,
        )

        rows_to_insert.append({
            "user_id": dj.USER_ID,
            "company": company,
            "role": resolved_role,
            "job_url": resolved_url,
            "posted_date": resolved_posted,
            "location": resolved_location or None,
            "work_arrangement": work_arrangement,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "match_score": match_score,
            "match_summary": match_summary,
            "source_site": f"{company} Careers · {source_type.title()}",
            "description": description or None,
            "external_job_id": resolved_url.rstrip("/").split("/")[-1],
            "source_type": source_type,
            "source_key": key,
            "decision": "new",
            "first_seen_at": now_iso,
            "last_seen_at": now_iso,
        })
        known.add(key)
        accepted += 1

    dj.insert_rows(rows_to_insert)

    print("")
    print("========== MULTI-ATS IMPORT SUMMARY ==========")
    print("Harvest rows checked:", checked)
    print("Excluded companies:", excluded_company)
    print("Title-filtered out:", title_rejected)
    print("Location-rejected:", location_rejected)
    print("Stale postings skipped:", stale)
    print("Active-TS postings excluded:", active_ts)
    print(f"Below {min_match_score}% match excluded:", low_match)
    print("Detail fetch unavailable (fallback used):", detail_fetch_failed)
    print("Already known / refreshed:", duplicates)
    print("NEW jobs inserted:", len(rows_to_insert))
    print("==============================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
