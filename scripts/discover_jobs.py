#!/usr/bin/env python3
import os,re,json,html,hashlib,urllib.parse
from datetime import datetime,timezone
import requests

SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY=os.environ['SUPABASE_SERVICE_ROLE_KEY']
USER_ID=os.environ['SUPABASE_USER_ID']
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG=json.load(open(os.path.join(ROOT,'config','sources.json'),encoding='utf-8'))
S=requests.Session(); S.headers.update({'User-Agent':'ToniJobDiscovery/1.0','Accept':'application/json,text/plain,*/*'})

US_HINTS=['united states','usa','u.s.','us remote','remote - us','remote, us','washington, dc','washington dc','district of columbia','maryland','virginia','arlington','mclean','herndon','reston','alexandria','bethesda','tysons','fairfax']
NON_US=['united kingdom','london','canada','toronto','germany','france','india','australia','singapore','japan','netherlands','poland','romania','czech','mexico','brazil','spain','italy','ireland']

def clean_html(t):
    t=html.unescape(t or '')
    t=re.sub(r'<br\s*/?>','\n',t,flags=re.I); t=re.sub(r'</p\s*>','\n',t,flags=re.I); t=re.sub(r'<[^>]+>',' ',t)
    return re.sub(r'\s+',' ',t).strip()

def norm(s): return re.sub(r'\s+',' ',(s or '').strip().lower())
def iso(v):
    m=re.match(r'^(\d{4}-\d{2}-\d{2})',str(v or '')); return m.group(1) if m else None

def salary(text):
    nums=[]
    for m in re.finditer(r'\$\s*([1-9]\d{1,2}(?:,\d{3})+|[1-9]\d{4,5})',text or ''):
        n=int(m.group(1).replace(',',''))
        if 50000<=n<=500000: nums.append(n)
    if len(nums)>=2:
        a,b=sorted(nums[:2]); return a,b
    return (nums[0],None) if nums else (None,None)

def title_ok(title):
    t=norm(title)
    if any(norm(x) in t for x in CFG.get('role_exclude',[])): return False
    return any(norm(x) in t for x in CFG.get('role_include',[]))

def loc_ok(loc):
    l=norm(loc)
    if CFG.get('allow_any_us_location'):
        if not l: return True
        return not (any(x in l for x in NON_US) and not any(x in l for x in US_HINTS))
    return (not l) or any(norm(x) in l for x in CFG.get('location_include',US_HINTS))

def key(kind,company,eid,url):
    raw='|'.join([kind,company,str(eid or ''),url or ''])
    return hashlib.sha256(raw.encode()).hexdigest()[:48]

def greenhouse(src):
    u=f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(src['token'])}/jobs?content=true"
    r=S.get(u,timeout=30); r.raise_for_status()
    for j in r.json().get('jobs',[]):
        d=clean_html(j.get('content')); lo=(j.get('location') or {}).get('name') or ''; a,b=salary(d)
        yield dict(company=src['company'],role=j.get('title') or '',job_url=j.get('absolute_url') or '',posted_date=iso(j.get('updated_at')),location=lo,salary_min=a,salary_max=b,source_site=f"Greenhouse · {src['company']}",description=d,external_job_id=str(j.get('id') or ''),source_type='greenhouse')

def lever(src):
    u=f"https://api.lever.co/v0/postings/{urllib.parse.quote(src['site'])}?mode=json"
    r=S.get(u,timeout=30); r.raise_for_status()
    for j in r.json():
        d=clean_html(' '.join([j.get('descriptionPlain') or '',j.get('additionalPlain') or ''])); lo=(j.get('categories') or {}).get('location') or ''; a,b=salary(d)
        yield dict(company=src['company'],role=j.get('text') or '',job_url=j.get('hostedUrl') or j.get('applyUrl') or '',posted_date=None,location=lo,salary_min=a,salary_max=b,source_site=f"Lever · {src['company']}",description=d,external_job_id=str(j.get('id') or ''),source_type='lever')

def ashby(src):
    u=f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(src['board'])}?includeCompensation=true"
    r=S.get(u,timeout=30); r.raise_for_status()
    for j in r.json().get('jobs',[]):
        d=clean_html(j.get('descriptionHtml') or j.get('description')); locs=[j.get('location') or '']+[x.get('location','') for x in (j.get('secondaryLocations') or []) if isinstance(x,dict)]; lo=' · '.join(x for x in locs if x)
        c=j.get('compensation') or {}; a=c.get('minValue') if isinstance(c,dict) else None; b=c.get('maxValue') if isinstance(c,dict) else None
        if not a and not b: a,b=salary(d)
        yield dict(company=src['company'],role=j.get('title') or '',job_url=j.get('jobUrl') or j.get('applyUrl') or '',posted_date=iso(j.get('publishedAt') or j.get('updatedAt')),location=lo,salary_min=a,salary_max=b,source_site=f"Ashby · {src['company']}",description=d,external_job_id=str(j.get('id') or j.get('jobPostingId') or ''),source_type='ashby')

FETCH={'greenhouse':greenhouse,'lever':lever,'ashby':ashby}

def existing():
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'}
    p={'select':'source_key','user_id':f'eq.{USER_ID}','source_key':'not.is.null','limit':'10000'}
    r=S.get(f'{SUPABASE_URL}/rest/v1/discovered_jobs',headers=h,params=p,timeout=30); r.raise_for_status()
    return {x['source_key'] for x in r.json() if x.get('source_key')}

def insert(rows):
    if not rows:return
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Prefer':'return=minimal,resolution=ignore-duplicates'}
    for i in range(0,len(rows),100):
        r=S.post(f'{SUPABASE_URL}/rest/v1/discovered_jobs',headers=h,data=json.dumps(rows[i:i+100]),timeout=30)
        if r.status_code>=300: raise RuntimeError(f'Supabase insert failed {r.status_code}: {r.text}')

def main():
    seen=existing(); scanned=kept=0; out=[]
    for src in CFG.get('sources',[]):
        if not src.get('enabled',True): continue
        kind=src.get('type'); print(f"Checking {src['company']} ({kind})")
        try: jobs=list(FETCH[kind](src))
        except Exception as e: print(' ERROR',e); continue
        for j in jobs:
            scanned+=1
            if not title_ok(j['role']) or not loc_ok(j['location']): continue
            k=key(j['source_type'],j['company'],j['external_job_id'],j['job_url'])
            if k in seen: continue
            now=datetime.now(timezone.utc).isoformat()
            out.append({'user_id':USER_ID,'company':j['company'],'role':j['role'],'job_url':j['job_url'] or None,'posted_date':j['posted_date'],'location':j['location'] or None,'salary_min':j['salary_min'],'salary_max':j['salary_max'],'source_site':j['source_site'],'description':j['description'] or None,'external_job_id':j['external_job_id'] or None,'source_type':j['source_type'],'source_key':k,'decision':'new','first_seen_at':now,'last_seen_at':now})
            seen.add(k); kept+=1
    insert(out); print(f'Scanned {scanned} postings; inserted {kept} new matching postings.')

if __name__=='__main__': main()
