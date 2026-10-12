"""Conservative price parsing for individual car listing pages.
Never scrape generic text for the first EUR amount: recommendations can contaminate it.
"""
import json
import re
from bs4 import BeautifulSoup

AMOUNT = re.compile(r'^\s*(?:EUR|€)?\s*([\d.,\s\u00a0\u202f]+)\s*(?:EUR|€)?\s*$', re.I)
PRICE_CONTEXT = re.compile(r'(?:kaufpreis|angebotspreis|asking price|verkaufspreis|price|prix|prijs|preis)', re.I)

def amount(value):
    if isinstance(value, (int,float)):
        n = round(value)
    else:
        if value is None: return None
        raw = str(value).strip().replace('\u00a0','').replace('\u202f','').replace(' ','')
        raw = re.sub(r'(?i)^(?:EUR|€)', '', raw)
        raw = re.sub(r'(?i)(?:EUR|€)$', '', raw)
        if not re.fullmatch(r'\d[\d.,]*',raw): return None
        if '.' in raw and ',' in raw:
            sep = '.' if raw.rfind('.') > raw.rfind(',') else ','
            raw = raw.replace(',' if sep == '.' else '.', '')
            raw = raw.replace(sep, '.')
        elif ',' in raw or '.' in raw:
            sep = ',' if ',' in raw else '.'
            pieces=raw.split(sep)
            if len(pieces)>2 or (len(pieces)==2 and len(pieces[-1])==3):
                raw=''.join(pieces)
            else: raw=raw.replace(sep,'.')
        try: n=round(float(raw))
        except ValueError: return None
    return n if 3000 <= n <= 500000 else None

def json_objects(node):
    if isinstance(node,list):
        for x in node: yield from json_objects(x)
    elif isinstance(node,dict):
        yield node
        for key in ('@graph','mainEntity','itemOffered','offers'):
            if key in node: yield from json_objects(node[key])

def extract_price(soup:BeautifulSoup):
    """Return (EUR price, method) only when currency explicitly EUR.
    Price in other currency is not silently converted to EUR.
    """
    for tag in soup.select('script[type="application/ld+json"]'):
        try: payload=json.loads(tag.string or tag.get_text())
        except (ValueError,TypeError): continue
        for obj in json_objects(payload):
            typ=str(obj.get('@type','')).lower()
            if typ not in ('offer','aggregateoffer'): continue
            if str(obj.get('priceCurrency','')).upper()!='EUR': continue
            price=amount(obj.get('price'))
            if price is not None and typ=='offer':return price,'jsonld_offer'
    for key,val in [('property','product:price:amount'),('property','og:price:amount'),('name','price'),('itemprop','price')]:
        for meta in soup.find_all('meta',attrs={key:val}):
            p=amount(meta.get('content'))
            if p is None:continue
            currency=soup.find('meta',attrs={'property':'product:price:currency'}) or soup.find('meta',attrs={'itemprop':'priceCurrency'})
            if currency and str(currency.get('content','')).upper()!='EUR':continue
            if not currency and val not in ('product:price:amount','og:price:amount'):continue
            return p,'meta_'+val
    # Look for explicitly labelled visible price nodes only, avoiding related-item cards.
    for selector in ('[data-testid="price"]','[itemprop="price"]','[class*="listing-price"]','[class*="offer-price"]'):
        for node in soup.select(selector)[:8]:
            raw=node.get('content') or node.get_text(' ',strip=True)
            if not re.search(r'€|\bEUR\b',str(raw),re.I):continue
            m=re.search(r'(?:€\s*|\bEUR\s*)([\d.,\s\u00a0]+)|([\d.,\s\u00a0]+)\s*(?:€|\bEUR\b)',str(raw),re.I)
            if m:
                p=amount(m.group(1) or m.group(2))
                if p is not None:return p,'labelled_dom'
    return None,'price_not_found'
