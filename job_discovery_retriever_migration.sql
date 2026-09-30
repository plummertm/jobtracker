-- Run this AFTER the first discovered_jobs setup SQL.
alter table public.discovered_jobs
  add column if not exists description text,
  add column if not exists external_job_id text,
  add column if not exists source_type text,
  add column if not exists first_seen_at timestamptz,
  add column if not exists last_seen_at timestamptz;

create index if not exists discovered_jobs_posted_date_idx
  on public.discovered_jobs(user_id, posted_date desc);

create index if not exists discovered_jobs_source_type_idx
  on public.discovered_jobs(user_id, source_type);
