# Free Job Discovery Retriever

This is the source-collection layer for the existing Job Discovery tab.

It checks public employer job-board APIs, filters for target presales/solutions roles, deduplicates results, and inserts new matches into Supabase `discovered_jobs`.

Supported now: Greenhouse, Lever, Ashby.

## Setup
1. Run `job_discovery_retriever_migration.sql` in Supabase SQL Editor.
2. In GitHub repo Settings > Secrets and variables > Actions, add:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_USER_ID`
3. Edit `config/sources.json` and enable company sources.
4. Push these files to the same GitHub repo as the tracker.
5. GitHub > Actions > Job Discovery Sync > Run workflow.
6. Refresh the Job Discovery tab.

Never place the Supabase service-role key in HTML, JavaScript, a public repo file, or screenshots. Keep it only in GitHub Actions Secrets.

## Source formats
Greenhouse board `https://job-boards.greenhouse.io/acme` => token `acme`.
Lever board `https://jobs.lever.co/acme` => site `acme`.
Ashby board `https://jobs.ashbyhq.com/Acme` => board `Acme`.

The workflow also runs automatically several times per day for $0/month.
