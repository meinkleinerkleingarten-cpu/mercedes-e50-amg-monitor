#!/usr/bin/env python3
"""E50 AMG V4.2: cautious multi-portal discovery; no login or access bypass."""
import csv
import datetime as dt
import hashlib
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
USER_AGENT = 'E50AMGMarketResearch/4.2 (+personal noncommercial market monitor; contact via GitHub repository)'
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
def robots_allowed(session,url,host):
 """Fail closed if robots.txt cannot be checked; cache per origin."""
 p=urlsplit(url); origin=f'{p.scheme}://{p.netloc}'
 if origin not in ROBOTS:
  robots_url=origin+'/robots.txt'
  try:
   r=session.get(robots_url,timeout=15,allow_redirects=False)
   if r.status_code != 200:
    ROBOTS[origin]=(False,'robots_http_'+str(r.status_code))
   else:
    rp=RobotFileParser();rp.parse(r.text.splitlines());ROBOTS[origin]=(rp,'ok')
  except requests.RequestException as e:
   ROBOTS[origin]=(False,'robots_unavailable_'+type(e).__name__)
 rp,reason=ROBOTS[origin]
 if rp is False:return False,reason
 return (rp.can_fetch(USER_AGENT,url),'robots_allowed' if rp.can_fetch(USER_AGENT,url) else 'robots_disallowed')

def fetch(session,url,host):
 if not permitted_host(url,host):raise ValueError('off_domain_url')
 allowed,reason=robots_allowed(session,url,host)
 if not allowed:raise RuntimeError(reason)
 r=session.get(url,timeout=22,allow_redirects=False)
 if r.status_code in (301,302,303,307,308):
  redirect=urljoin(url,r.headers.get('Location',''))
  if not permitted_host(redirect,host):raise ValueError('redirect_off_domain')
  allowed,reason=robots_allowed(session,redirect,host)
  if not allowed:raise RuntimeError(reason)
  r=session.get(redirect,timeout=22,allow_redirects=False)
 if r.status_code in (401,403,429):raise RuntimeError('access_restricted_'+str(r.status_code))
 r.raise_for_status()
 if 'html' not in r.headers.get('content-type','text/html').lower():raise ValueError('non_html')
 return BeautifulSoup(r.text,'html.parser')

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

def extract(url,soup):
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
 country=extract_country(soup)
 return {'id':hashlib.sha256(canonical(url).encode()).hexdigest()[:20], 'country':country or '??','year':year,'title':heading[:300], 'url':canonical(url),'price_local':price,'currency':'EUR' if price is not None else None,'price_eur':price,'status':'inactive' if inactive else 'unverified','last_seen':dt.date.today().isoformat()},'accepted'

def init(con):
 con.execute('CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY,country TEXT,year INTEGER,title TEXT,url TEXT,price_local REAL,currency TEXT,price_eur REAL,status TEXT,last_seen TEXT)')
 con.execute('CREATE TABLE IF NOT EXISTS snapshots (snapshot_date TEXT,id TEXT,price_eur REAL,PRIMARY KEY(snapshot_date,id))')

def save(con,row):
 con.execute('''INSERT INTO listings VALUES (:id,:country,:year,:title,:url,:price_local,:currency,:price_eur,:status,:last_seen)
 ON CONFLICT(id) DO UPDATE SET country=CASE WHEN excluded.country='??' THEN listings.country ELSE excluded.country END,
 year=COALESCE(excluded.year,listings.year),title=excluded.title,
 price_local=COALESCE(excluded.price_local,listings.price_local),currency=COALESCE(excluded.currency,listings.currency),
 price_eur=COALESCE(excluded.price_eur,listings.price_eur),
 status=CASE WHEN excluded.status='inactive' THEN 'inactive' ELSE listings.status END,
 last_seen=excluded.last_seen''',row)
 con.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)',(row['last_seen'],row['id'],row['price_eur']))

def exports(con):
 with (ROOT/'candidates.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['country','year','title','price_local','currency','price_eur','status','url','last_seen'])
  w.writerows(con.execute('SELECT country,year,title,price_local,currency,price_eur,status,url,last_seen FROM listings ORDER BY country,year'))
 groups={}
 for c,y,p in con.execute("SELECT country,year,price_eur FROM listings WHERE status='verified_live' AND country!='??' AND year IN (1996,1997) AND price_eur IS NOT NULL"):
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
   print('PORTAL',source['name'],'candidate_links',len(found),flush=True)
   counters['sources_ok']+=1;urls.update({u:source for u in found})
  except Exception as e:
   counters['sources_failed']+=1;print('PORTAL_ERROR',source['name'],str(e)[:150],flush=True)
  time.sleep(float(os.getenv('REQUEST_DELAY','1.5')))
 for url,source in urls.items():
  try:
   soup=fetch(session,url,source['host']);row,reason=extract(url,soup);counters[reason]+=1
   if row:save(con,row);print('FOUND',row['country'],row['year'],row['price_eur'],row['url'],flush=True)
  except Exception as e:
   counters['detail_failed']+=1;print('DETAIL_ERROR',url,str(e)[:120],flush=True)
  time.sleep(float(os.getenv('REQUEST_DELAY','1.5')))
 con.commit();verified=exports(con)
 print('RUN_SUMMARY',json.dumps(dict(counters),sort_keys=True),'stored',con.execute('SELECT COUNT(*) FROM listings').fetchone()[0],'verified_priced',verified,flush=True)
 con.close()
 return 1 if counters['sources_ok']==0 else 0

if __name__=='__main__':sys.exit(main())
