#!/usr/bin/env python3
"""E50 AMG V4.2: cautious multi-portal discovery; no login or access bypass."""
import csv
import datetime as dt
import hashlib
import difflib
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from price_extract import extract_price

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'listings.sqlite3'
USER_AGENT = 'E50AMGMarketResearch/4.3 (+personal noncommercial market monitor; contact via GitHub repository)'
HEADERS = {'User-Agent': USER_AGENT, 'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8'}
MODEL = re.compile(r'(?<!\w)(?:E\s*[- ]?\s*50\s*AMG|AMG\s*E\s*[- ]?\s*50)(?!\w)', re.I)
BAD = re.compile(r'(?<!\w)(?:E\s*55\s*AMG|E\s*53\s*AMG|E\s*500|E\s*50\s*(?:badge|logo|emblem|aufkleber|sticker|teile|parts))(?!\w)', re.I)
YEARS = re.compile(r'(?<!\d)(1996|1997)(?!\d)')
COUNTRY_NAMES = {'Deutschland':'DE','Germany':'DE','Niederlande':'NL','Netherlands':'NL','Österreich':'AT','Austria':'AT','Frankreich':'FR','France':'FR','Schweden':'SE','Sweden':'SE','Schweiz':'CH','Switzerland':'CH','Belgien':'BE','Belgium':'BE','Polen':'PL','Poland':'PL','Italien':'IT','Italy':'IT','Spanien':'ES','Spain':'ES','Tschechien':'CZ','Czechia':'CZ','United Kingdom':'GB','Großbritannien':'GB','Japan':'JP','Australia':'AU','Australien':'AU','USA':'US'}

# Experimental URLs are starting points, NOT verified APIs or promises of access.
# Access restrictions are respected; disabled sources are documented rather than probed.
SOURCES = [
 {'name':'Classic Trader W210', 'host':'classic-trader.com', 'url':'https://www.classic-trader.com/de/automobile/suche/mercedes-benz/e-klasse/w-210', 'link_regex':r'/automobile/inserat/[^?#]+/e-50-amg/(?:1996|1997)/\d+', 'enabled':True},
 {'name':'Kleinanzeigen DE (experimental)', 'host':'kleinanzeigen.de', 'url':'https://www.kleinanzeigen.de/s-autos/mercedes-e50-amg/k0c216', 'link_regex':r'/s-anzeige/[^?#]+/\d+-216-[^/?#]+', 'enabled':True},
 {'name':'Marktplaats NL (experimental)', 'host':'marktplaats.nl', 'url':'https://www.marktplaats.nl/q/mercedes+e50+amg/', 'link_regex':r'/v/auto.s/[^?#]+/m\d+', 'enabled':True},
 {'name':'Willhaben AT (experimental)', 'host':'willhaben.at', 'url':'https://www.willhaben.at/iad/gebrauchtwagen/auto/gebrauchtwagenboerse?keyword=E%2050%20AMG', 'link_regex':r'/iad/gebrauchtwagen/d/[^?#]+-\d+', 'enabled':True},
 {'name':'OTOMOTO PL (experimental)', 'host':'otomoto.pl', 'url':'https://www.otomoto.pl/osobowe/q-e-50-amg', 'link_regex':r'/osobowe/oferta/[^?#]+-ID[a-zA-Z0-9]+', 'enabled':True},
 {'name':'AutoScout24 E50 (experimental)', 'host':'autoscout24.com', 'url':'https://www.autoscout24.com/lst/mercedes-benz/e-50-amg', 'link_regex':r'/offers/[a-z0-9-]+', 'enabled':True},
 {'name':'Car & Classic (403 previously)', 'host':'carandclassic.com', 'url':'https://www.carandclassic.com/list/31/e50%20amg/', 'link_regex':r'/car/C\d+', 'enabled':False},
 {'name':'mobile.de (awaiting API)', 'host':'mobile.de', 'url':'https://suchen.mobile.de/fahrzeuge/search.html?vc=Car&dam=false&isSearchRequest=true&ms=17200%3B%3B%3BE%2050%20AMG', 'link_regex':r'/(?:auto-inserat|fahrzeuge/details)/[^?#]+', 'enabled':False},
]

def canonical(url):
 p=urlsplit(url)
 return urlunsplit((p.scheme.lower(),(p.hostname or '').lower().removeprefix('www.'),p.path.rstrip('/') or '/','',''))

def permitted_host(url,host):
 h=(urlsplit(url).hostname or '').lower()
 return h==host or h.endswith('.'+host)

ROBOTS = {}
MAX_REDIRECTS = 5
MAX_PER_PORTAL = max(1, int(os.getenv('MAX_PER_PORTAL', '12')))


def robots_allowed(session, url, host):
 """Check robots.txt including redirects; deny on unresolved policy.

 Redirects to another host are not followed by this conservative crawler.
 A redirect is not itself a Disallow, but policy must be fetched first.
 """
 p = urlsplit(url)
 origin = f'{p.scheme}://{p.netloc}'
 if origin not in ROBOTS:
  robots_url = origin + '/robots.txt'
  try:
   seen = set()
   for _ in range(MAX_REDIRECTS + 1):
    if robots_url in seen:
     ROBOTS[origin] = (False, 'robots_redirect_loop')
     break
    seen.add(robots_url)
    r = session.get(robots_url, timeout=15, allow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308):
     location = r.headers.get('Location')
     if not location:
      ROBOTS[origin] = (False, 'robots_redirect_missing_location')
      break
     target = urljoin(robots_url, location)
     if urlsplit(target).scheme != 'https' or not permitted_host(target, host):
      ROBOTS[origin] = (False, 'robots_redirect_off_domain')
      break
     robots_url = target
     continue
    if r.status_code == 200:
     rp = RobotFileParser()
     rp.parse(r.text.splitlines())
     ROBOTS[origin] = (rp, 'ok')
    elif r.status_code in (404, 410):
     # RFC 9309: unavailable robots file -> no rules.
     rp = RobotFileParser()
     rp.parse([])
     ROBOTS[origin] = (rp, 'robots_not_found')
    else:
     ROBOTS[origin] = (False, 'robots_http_' + str(r.status_code))
    break
   else:
    ROBOTS[origin] = (False, 'robots_too_many_redirects')
  except requests.RequestException as e:
   ROBOTS[origin] = (False, 'robots_unavailable_' + type(e).__name__)
 rp, reason = ROBOTS[origin]
 if rp is False:
  return False, reason
 allowed = rp.can_fetch(USER_AGENT, url)
 return allowed, 'robots_allowed' if allowed else 'robots_disallowed'


def fetch(session, url, host):
 if not permitted_host(url, host):
  raise ValueError('off_domain_url')
 current = url
 for _ in range(MAX_REDIRECTS + 1):
  allowed, reason = robots_allowed(session, current, host)
  if not allowed:
   raise RuntimeError(reason)
  r = session.get(current, timeout=22, allow_redirects=False)
  if r.status_code in (301, 302, 303, 307, 308):
   location = r.headers.get('Location')
   if not location:
    raise RuntimeError('redirect_missing_location')
   target = urljoin(current, location)
   if urlsplit(target).scheme != 'https' or not permitted_host(target, host):
    raise ValueError('redirect_off_domain')
   current = target
   continue
  if r.status_code in (401, 403, 429):
   raise RuntimeError('access_restricted_' + str(r.status_code))
  r.raise_for_status()
  if 'html' not in r.headers.get('content-type', 'text/html').lower():
   raise ValueError('non_html')
  return BeautifulSoup(r.text, 'html.parser')
 raise RuntimeError('too_many_redirects')


def plausible_e50_candidate(url, title):
 """Strict model filter; do not infer model from generic search results."""
 text = (title + ' ' + urlsplit(url).path.replace('-', ' ')).lower()
 if re.search(r'\b(?:suche|gesucht|wanted|searching for|teile|parts|badge|emblem|logo|sticker|aufkleber)\b', text):
  return False
 if re.search(r'\b(?:e\s*55|e\s*53|e\s*63|e\s*500|gle|glc|cla|s\s*55|e\s*350|e\s*200)\b', text):
  return False
 return bool(MODEL.search(text))

def discover(soup,source):
 found={};rx=re.compile(source['link_regex'],re.I)
 for a in soup.select('a[href]'):
  u=canonical(urljoin(source['url'],a['href']))
  if permitted_host(u,source['host']) and rx.search(urlsplit(u).path):found[u]=a.get_text(' ',strip=True)
 return found

def extract_country(soup):
 for im in soup.select('img[alt],img[title]'):
  desc=(im.get('alt','')+' '+im.get('title','')).strip()
  m=re.search(r'\b(DE|NL|AT|FR|SE|CH|BE|PL|IT|ES|CZ|GB|JP|AU|US)\s*flag\b',desc,re.I)
  if m:return m.group(1).upper()
 text=soup.get_text(' ',strip=True)
 for name,code in COUNTRY_NAMES.items():
  if re.search(r'(?:Standort|Location|Ort|Localisation|Pays|Country)\s*:?\s*'+re.escape(name)+r'\b',text,re.I):return code
 return None

# The portal's country is a search-location hint, not proof of the car's location.
PORTAL_COUNTRY = {'kleinanzeigen.de':'DE', 'marktplaats.nl':'NL',
                  'willhaben.at':'AT', 'otomoto.pl':'PL'}


def structured_country(soup):
 """Use explicit location in JSON-LD where possible; avoid seller-wide addresses."""
 for tag in soup.select('script[type="application/ld+json"]'):
  try: data=json.loads(tag.string or tag.get_text())
  except (ValueError,TypeError): continue
  def walk(obj):
   if isinstance(obj,list):
    for child in obj: yield from walk(child)
   elif isinstance(obj,dict):
    yield obj
    for key in ('@graph','itemOffered','mainEntity','offers'):
     if key in obj: yield from walk(obj[key])
  for obj in walk(data):
   for field in ('availableAtOrFrom','itemLocation','location'):
    place=obj.get(field)
    if isinstance(place,list): place=place[0] if place else None
    if not isinstance(place,dict): continue
    address=place.get('address',place)
    if not isinstance(address,dict): continue
    country=address.get('addressCountry')
    if isinstance(country,dict): country=country.get('name')
    if not isinstance(country,str): continue
    country=country.strip()
    if country.upper() in COUNTRY_NAMES.values():return country.upper()
    for name,code in COUNTRY_NAMES.items():
     if country.lower()==name.lower():return code
 return None


def country_for_listing(soup,source):
 code=structured_country(soup) or extract_country(soup)
 if code:return code,'listing_location'
 fallback=PORTAL_COUNTRY.get(source['host'])
 if fallback:return fallback,'portal_hint'
 return '??','unknown'


def listing_vin(soup):
 """VIN is a strong cross-portal identity, but only when clearly labelled."""
 text=soup.get_text(' ',strip=True)
 m=re.search(r'(?:VIN|Fahrgestellnummer|Vehicle identification number|Chassis number|Numer VIN)\s*[:#]?\s*([A-HJ-NPR-Z0-9]{17})(?![A-Z0-9])',text,re.I)
 return m.group(1).upper() if m else None


def listing_features(soup):
 """Extract explicit mileage/color and photo URLs, never infer from page chrome."""
 text=soup.get_text(' ',strip=True)
 km=None
 for pattern in (r'(?:Kilometerstand|Mileage|Kilometerstand|Kilometerstand\s*\(km\)|Laufleistung|Kilometerstand|Kilometer|Kilometrage|Kilometerstand|Przebieg)\s*:?\s*([\d.,\s]{2,12})\s*km',
                 r'([\d.,\s]{2,12})\s*km\s*(?:Laufleistung|Mileage)'):
  match=re.search(pattern,text,re.I)
  if match:
   raw=re.sub(r'\D','',match.group(1))
   if raw and 100 <= int(raw) <= 1000000:km=int(raw);break
 color=None
 match=re.search(r'(?:Außenfarbe|Exterior colo[u]?r|Farbe\s+außen|Kleur|Kolor nadwozia)\s*:?\s*(schwarz|black|zwart|czarny|silber|silver|zilver|srebrny|weiß|white|wit|biały|blau|blue|blauw|niebieski|grau|grey|gray|grijs|szary|rot|red|rood|czerwony|grün|green|groen|zielony)\b',text,re.I)
 if match:
  mapping={'schwarz':'black','zwart':'black','czarny':'black','silber':'silver','zilver':'silver','srebrny':'silver','weiß':'white','wit':'white','biały':'white','blau':'blue','blauw':'blue','niebieski':'blue','grau':'grey','gray':'grey','grijs':'grey','szary':'grey','rot':'red','rood':'red','czerwony':'red','grün':'green','groen':'green','zielony':'green'}
  val=match.group(1).lower();color=mapping.get(val,val)
 images=[]
 for tag in soup.select('script[type="application/ld+json"]'):
  try: data=json.loads(tag.string or tag.get_text())
  except (ValueError,TypeError):continue
  def visit(o):
   if isinstance(o,list):
    for x in o:yield from visit(x)
   elif isinstance(o,dict):
    yield o
    for k in ('@graph','mainEntity','itemOffered'):
     if k in o:yield from visit(o[k])
  for obj in visit(data):
   typ=obj.get('@type','')
   if isinstance(typ,list):typ=' '.join(typ)
   if not re.search(r'Vehicle|Car|Product',str(typ),re.I):continue
   for image in (obj.get('image') if isinstance(obj.get('image'),list) else [obj.get('image')]):
    if isinstance(image,dict):image=image.get('url') or image.get('contentUrl')
    if isinstance(image,str) and image.startswith('https://'):
     images.append(image)
 return km,color,list(dict.fromkeys(images))[:8]


def confirmed_observation_days(con, listing_id):
 """Count distinct days with an actual successfully fetched structured offer."""
 return con.execute("SELECT COUNT(DISTINCT observed_date) FROM listing_observations WHERE id=? AND evidence='structured_offer_seen'",(listing_id,)).fetchone()[0]


def listing_observation_status(soup,price,method,inactive):
 if inactive:return 'inactive','explicit_inactive_text'
 if price is None:return 'unverified','price_missing'
 # JSON-LD offer supports an advertised price, not definitive sale availability.
 if method=='jsonld_offer':return 'observed_live','structured_offer_seen'
 return 'unverified','weaker_price_evidence'

def extract(url,soup,source):
 title=soup.title.get_text(' ',strip=True) if soup.title else ''
 h1=soup.find('h1');heading=h1.get_text(' ',strip=True) if h1 else title
 text=soup.get_text(' ',strip=True)
 if not MODEL.search(heading+' '+title) or BAD.search(heading+' '+title):return None,'wrong_model'
 title_years=set(map(int,YEARS.findall(heading+' '+title)))
 year=next(iter(title_years)) if len(title_years)==1 else None
 if year is None:
  for label in ('Baujahr','Year of manufacture','Année de construction','Bouwjaar'):
   m=re.search(re.escape(label)+r'\s*:?\s*(1996|1997)',text,re.I)
   if m:year=int(m.group(1));break
 if year not in (1996,1997):return None,'unknown_year'
 inactive=bool(re.search(r'(?:this listing has now been removed|dieses inserat wurde.*entfernt|this advert has been removed|sold or otherwise removed)',text,re.I))
 price,method=extract_price(soup)
 # Never mistake Polish zloty for EUR. Currency conversion comes in a later release.
 if permitted_host(url,'otomoto.pl'):
  price=None;method='currency_unverified_PLN_skipped'
 print('PRICE_CHECK',method,price,url,flush=True)
 country,country_basis=country_for_listing(soup,source)
 status,verification_basis=listing_observation_status(soup,price,method,inactive)
 vin=listing_vin(soup)
 mileage,color,images=listing_features(soup)
 return {'id':hashlib.sha256(canonical(url).encode()).hexdigest()[:20], 'country':country,'country_basis':country_basis,'vin':vin,'mileage_km':mileage,'exterior_color':color,'image_urls':json.dumps(images),'verification_basis':verification_basis,'year':year,'title':heading[:300], 'url':canonical(url),'price_local':price,'currency':'EUR' if price is not None else None,'price_eur':price,'status':status,'last_seen':dt.date.today().isoformat()},'accepted'

def init(con):
 con.execute('CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY,country TEXT,year INTEGER,title TEXT,url TEXT,price_local REAL,currency TEXT,price_eur REAL,status TEXT,last_seen TEXT)')
 con.execute('CREATE TABLE IF NOT EXISTS snapshots (snapshot_date TEXT,id TEXT,price_eur REAL,PRIMARY KEY(snapshot_date,id))')
 columns={r[1] for r in con.execute('PRAGMA table_info(listings)')}
 for name,kind in [('country_basis','TEXT'),('vin','TEXT'),('verification_basis','TEXT'),
                   ('first_seen','TEXT'),('seen_days','INTEGER DEFAULT 0'),
                   ('duplicate_group','TEXT'),('duplicate_review','TEXT'),('mileage_km','INTEGER'),('exterior_color','TEXT'),('image_urls','TEXT')]:
  if name not in columns:con.execute(f'ALTER TABLE listings ADD COLUMN {name} {kind}')
 con.execute('CREATE TABLE IF NOT EXISTS listing_observations (id TEXT,observed_date TEXT,evidence TEXT,PRIMARY KEY(id,observed_date))')


def save(con,row):
 old=con.execute('SELECT last_seen,seen_days,status,country,country_basis FROM listings WHERE id=?',(row['id'],)).fetchone()
 if old:
  old_date,old_days,old_status,old_country,old_basis=old
  days=max(1,old_days or 1)+(1 if old_date!=row['last_seen'] else 0)
  if old_basis=='listing_location' and row['country_basis']!='listing_location':
   row['country'],row['country_basis']=old_country,old_basis
 else:days=1
 status=row['status']
 if status=='observed_live':
  con.execute('INSERT OR REPLACE INTO listing_observations (id,observed_date,evidence) VALUES (?,?,?)',
              (row['id'],row['last_seen'],'structured_offer_seen'))
  if confirmed_observation_days(con,row['id'])>=2:
   status='verified_live'
   row['verification_basis']='structured_offer_seen_on_multiple_days'
 # A previously confirmed ad may become unverifiable; do not silently retain verified status.
 row['status']=status
 row['seen_days']=days
 con.execute('''INSERT INTO listings
 (id,country,year,title,url,price_local,currency,price_eur,status,last_seen,
  country_basis,vin,verification_basis,first_seen,seen_days,mileage_km,exterior_color,image_urls)
 VALUES (:id,:country,:year,:title,:url,:price_local,:currency,:price_eur,:status,:last_seen,
  :country_basis,:vin,:verification_basis,:last_seen,:seen_days,:mileage_km,:exterior_color,:image_urls)
 ON CONFLICT(id) DO UPDATE SET
 country=excluded.country,country_basis=excluded.country_basis,
 year=excluded.year,title=excluded.title,
 price_local=excluded.price_local,currency=excluded.currency,price_eur=excluded.price_eur,
 status=excluded.status,last_seen=excluded.last_seen,
 vin=COALESCE(excluded.vin,listings.vin),mileage_km=COALESCE(excluded.mileage_km,listings.mileage_km),
 exterior_color=COALESCE(excluded.exterior_color,listings.exterior_color),
 image_urls=CASE WHEN excluded.image_urls IS NOT NULL AND excluded.image_urls!='[]' THEN excluded.image_urls ELSE listings.image_urls END,
 verification_basis=excluded.verification_basis,
 first_seen=COALESCE(listings.first_seen,excluded.first_seen),seen_days=excluded.seen_days''',row)
 con.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)',(row['last_seen'],row['id'],row['price_eur']))


def flag_duplicates(con):
 """Confirm same VIN; score other pairs conservatively for manual review."""
 con.execute('UPDATE listings SET duplicate_group=NULL,duplicate_review=NULL')
 rows=[dict(zip(['id','country','year','price','vin','km','color','images','title'],r)) for r in
       con.execute('SELECT id,country,year,price_eur,vin,mileage_km,exterior_color,image_urls,title FROM listings')]
 for r in rows:
  if r['vin']:
   con.execute('UPDATE listings SET duplicate_group=? WHERE id=?',('vin:'+r['vin'],r['id']))
 reviews=set()
 for i,a in enumerate(rows):
  for b in rows[i+1:]:
   if a['vin'] and b['vin']:
    if a['vin']==b['vin']:
     print('DUPLICATE_VIN_PAIR',a['id'],b['id'],flush=True)
    continue
   if a['year']!=b['year'] or a['country']!=b['country'] or a['country']=='??':continue
   # Contradictory attributes make a duplicate less likely.
   if a['color'] and b['color'] and a['color']!=b['color']:continue
   if a['km'] and b['km'] and abs(a['km']-b['km'])>2000:continue
   evidence=[]
   if a['price'] is not None and a['price']==b['price']:evidence.append('same_price')
   if a['km'] and b['km'] and abs(a['km']-b['km'])<=500:evidence.append('matching_mileage')
   if a['color'] and b['color'] and a['color']==b['color']:evidence.append('matching_color')
   ai=set(json.loads(a['images'] or '[]'));bi=set(json.loads(b['images'] or '[]'))
   if ai & bi:evidence.append('identical_image_url')
   if evidence:
    label='possible_same_vehicle:'+','.join(evidence)
    for r in (a,b):
     reviews.add(r['id'])
     con.execute('UPDATE listings SET duplicate_review=? WHERE id=?',(label,r['id']))
 print('DUPLICATE_REVIEW',len(reviews),'listings need manual review',flush=True)


def exports(con):
 with (ROOT/'candidates.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f)
  w.writerow(['country','country_basis','year','title','price_local','currency','price_eur','status','verification_basis','seen_days','duplicate_group','duplicate_review','mileage_km','exterior_color','image_urls','url','last_seen'])
  w.writerows(con.execute('''SELECT country,country_basis,year,title,price_local,currency,price_eur,status,verification_basis,seen_days,duplicate_group,duplicate_review,mileage_km,exterior_color,image_urls,url,last_seen FROM listings ORDER BY country,year'''))
 # Country-level statistics must not silently count a portal guess as verified geography.
 # Multiple ads with the same VIN count as one vehicle; unconfirmed price matches remain separate.
 groups={};seen=set()
 for c,y,p,vin,identity in con.execute("SELECT country,year,price_eur,vin,id FROM listings WHERE last_seen=? AND status='verified_live' AND country_basis='listing_location' AND country!='??' AND year IN (1996,1997) AND price_eur IS NOT NULL AND duplicate_review IS NULL",(dt.date.today().isoformat(),)):
  key=('vin',vin) if vin else ('id',identity)
  if key in seen:continue
  seen.add(key)
  groups.setdefault((c,y),[]).append(p)
 with (ROOT/'country_summary.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['country','year','n','mean_eur','median_eur','min_eur','max_eur'])
  for (c,y),ps in sorted(groups.items()):w.writerow([c,y,len(ps),round(statistics.mean(ps),2),round(statistics.median(ps),2),min(ps),max(ps)])
 return sum(map(len,groups.values()))

def main():
 con=sqlite3.connect(DB);init(con)
 session=requests.Session();session.headers.update(HEADERS)
 counters=Counter();urls={}
 for source in SOURCES:
  if not source['enabled']:
   print('PORTAL_SKIPPED',source['name'],'disabled_pending_permission',flush=True)
   continue
  try:
   soup=fetch(session,source['url'],source['host']);found=discover(soup,source)
   selected=[u for u,title in found.items() if plausible_e50_candidate(u,title)]
   print('PORTAL',source['name'],'candidate_links',len(found),'plausible',len(selected),'filtered',len(found)-len(selected),flush=True)
   for u in selected[:MAX_PER_PORTAL]:
    urls[u]=source
   if len(selected)>MAX_PER_PORTAL:
    print('PORTAL_LIMIT',source['name'],'skipped',len(selected)-MAX_PER_PORTAL,flush=True)
   counters['sources_ok']+=1
  except Exception as e:
   counters['sources_failed']+=1;print('PORTAL_ERROR',source['name'],str(e)[:150],flush=True)
  time.sleep(float(os.getenv('REQUEST_DELAY','1.5')))
 for url,source in urls.items():
  try:
   soup=fetch(session,url,source['host']);row,reason=extract(url,soup,source);counters[reason]+=1
   if row:save(con,row);print('FOUND',row['country'],row['year'],row['price_eur'],row['url'],flush=True)
  except Exception as e:
   counters['detail_failed']+=1;print('DETAIL_ERROR',url,str(e)[:120],flush=True)
  time.sleep(float(os.getenv('REQUEST_DELAY','1.5')))
 flag_duplicates(con);con.commit();verified=exports(con)
 print('RUN_SUMMARY',json.dumps(dict(counters),sort_keys=True),'stored',con.execute('SELECT COUNT(*) FROM listings').fetchone()[0],'verified_priced',verified,'observed',con.execute("SELECT COUNT(*) FROM listings WHERE status='observed_live'").fetchone()[0],flush=True)
 con.close()
 return 1 if counters['sources_ok']==0 else 0

if __name__=='__main__':sys.exit(main())
