#!/usr/bin/env python3
"""Manually verify a candidate; never auto-certify a search result as live."""
import argparse,sqlite3
from pathlib import Path
p=argparse.ArgumentParser(description='Manually review a candidate after opening its source URL')
p.add_argument('id',help='Listing id from SQLite');p.add_argument('status',choices=['verified_live','inactive','sold','duplicate','rejected','unverified']);p.add_argument('--vin',help='Optional confirmed VIN for duplicate checks')
a=p.parse_args();db=Path(__file__).resolve().parent/'listings.sqlite3';con=sqlite3.connect(db)
if a.vin:
 vin=a.vin.upper().strip()
 if len(vin)!=17 or not vin.isalnum() or any(x in vin for x in 'IOQ'):p.error('VIN must have 17 valid characters')
 con.execute('CREATE TABLE IF NOT EXISTS vins (id TEXT PRIMARY KEY,vin TEXT)')
 existing=con.execute('SELECT id FROM vins WHERE vin=? AND id<>?',(vin,a.id)).fetchone()
 if existing and a.status=='verified_live':p.error('VIN already registered for '+existing[0]+'; review as duplicate')
 con.execute('INSERT OR REPLACE INTO vins VALUES (?,?)',(a.id,vin))
cur=con.execute('UPDATE listings SET status=? WHERE id=?',(a.status,a.id))
if cur.rowcount!=1:p.error('Listing id not found')
con.commit();print('Updated',a.id,'to',a.status)
