#!/usr/bin/env python3
"""E50 AMG W210 discovery bot. Public search RSS, conservative validation, SQLite history."""
import csv, datetime as dt, hashlib, html, json, os, re, sqlite3, statistics, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'listings.sqlite3'
SITES={
'DE':['mobile.de','classic-trader.com','kleinanzeigen.de'],
'FR':['leboncoin.fr','occasion.largus.fr'],
'NL':['marktplaats.nl','autouncle.nl','classic.com'],
'GB':['carandclassic.com','collectingcars.com'],
'SE':['blocket.se','minannons.se'],
'AT':['willhaben.at'],
'CH':['autoscout24.ch'],
'PL':['otomoto.pl'],
'CZ':['sauto.cz'],
'IT':['subito.it'],
'ES':['coches.net'],
'JP':['goo-net-exchange.com','carsensor.net'],
'US':['bringatrailer.com','classic.com'],
'AU':['carsales.com.au']}
CURRENCY={'DE':'EUR','FR':'EUR','NL':'EUR','GB':'GBP','SE':'SEK','AT':'EUR','CH':'CHF','PL':'PLN','CZ':'CZK','IT':'EUR','ES':'EUR','JP':'JPY','US':'USD','AU':'AUD'}
# Optional manually provided FX rates: JSON e.g. {"SEK":0.09}; EUR always 1.
FX={'EUR':1.0, **json.loads(os.getenv('FX_EUR_JSON','{}'))}
UA='Mozilla/5.0 (compatible; E50AMGResearchBot/1.0; public-search-only)'

def fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/rss+xml,application/xml,text/xml'})
 with urllib.request.urlopen(req,timeout=18) as resp: return resp.read()

def discover(domain):
 q=f'"E 50 AMG" (1996 OR 1997) site:{domain}'
 url='https://www.bing.com/search?'+urllib.parse.urlencode({'q':q,'format':'rss'})
 try: root=ET.fromstring(fetch(url))
 except Exception as e:
  print('DISCOVERY_FAILED',domain,str(e)[:140]);return []
 return [{'title':(x.findtext('title') or ''),'url':(x.findtext('link') or ''),'snippet':(x.findtext('description') or '')} for x in root.findall('./channel/item')]

def normalize(item,country):
 text=html.unescape(re.sub('<[^>]+>',' ',item['title']+' '+item['snippet']))
 if not re.search(r'\bE\s*[- ]?\s*50\s*AMG\b',text,re.I):return None
 if re.search(r'\b(E\s*55|E\s*500|E\s*53)\b',text,re.I):return None
 years=set(int(y) for y in re.findall(r'\b(1996|1997)\b',text))
 # Ambiguous 1996 OR 1997 search result must not be assigned to either year.
 year=next(iter(years)) if len(years)==1 else None
 if not year:return None
 price=None;currency=CURRENCY[country]
 patterns=[r'(?<!\d)(\d{1,3}(?:[. ,]\d{3})+|\d{4,7})\s*(?:€|EUR)\b?',r'(?:€|EUR)\s*(\d{1,3}(?:[. ,]\d{3})+|\d{4,7})'] if currency=='EUR' else []
 for p in patterns:
  m=re.search(p,text,re.I)
  if m:
   val=int(re.sub(r'\D','',m.group(1)))
   if 3000<=val<=500000:price=val;break
 url=item['url'].split('#')[0].split('?')[0]
 return {'id':hashlib.sha256(url.encode()).hexdigest()[:20],'country':country,'year':year,'title':item['title'],'url':url,'price_local':price,'currency':currency,'price_eur':round(price*FX[currency],2) if price is not None and currency in FX else None,'status':'unverified','last_seen':dt.date.today().isoformat()}

def main():
 con=sqlite3.connect(DB)
 con.execute('CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY,country TEXT,year INTEGER,title TEXT,url TEXT,price_local REAL,currency TEXT,price_eur REAL,status TEXT,last_seen TEXT)')
 con.execute('CREATE TABLE IF NOT EXISTS snapshots (snapshot_date TEXT,id TEXT,price_eur REAL,PRIMARY KEY(snapshot_date,id))')
 for country,domains in SITES.items():
  for domain in domains:
   for item in discover(domain):
    row=normalize(item,country)
    if row:
     con.execute('INSERT INTO listings VALUES (:id,:country,:year,:title,:url,:price_local,:currency,:price_eur,:status,:last_seen) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,title=excluded.title,price_local=COALESCE(excluded.price_local,listings.price_local),price_eur=COALESCE(excluded.price_eur,listings.price_eur)',row)
     con.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)',(row['last_seen'],row['id'],row['price_eur']))
 con.commit()
 with (ROOT/'candidates.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['country','year','title','price_local','currency','price_eur','status','url','last_seen'])
  w.writerows(con.execute('SELECT country,year,title,price_local,currency,price_eur,status,url,last_seen FROM listings ORDER BY country,year'))
 # Only verified, live listings are eligible for country price averages.
 data=list(con.execute("SELECT country,year,price_eur FROM listings WHERE status='verified_live' AND price_eur IS NOT NULL"))
 groups={}
 for c,y,p in data:groups.setdefault((c,y),[]).append(p)
 with (ROOT/'country_summary.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['country','year','n','mean_eur','median_eur','min_eur','max_eur'])
  for (c,y),prices in sorted(groups.items()):w.writerow([c,y,len(prices),round(statistics.mean(prices),2),round(statistics.median(prices),2),min(prices),max(prices)])
 print('Candidate listings:',con.execute('SELECT count(*) FROM listings').fetchone()[0],'; verified for averages:',len(data))
 con.close()
if __name__=='__main__':main()
