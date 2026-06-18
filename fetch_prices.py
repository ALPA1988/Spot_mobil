import urllib.request
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone, timedelta

TOKEN = '930c01dc-e1bc-43e8-9f32-30c741099051'
DOMAIN = '10YAT-APG------L'
NS = {'ns': 'urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3'}

VIENNA = timezone(timedelta(hours=2))  # CEST; im Winter +1 nehmen

def fetch_day(local_date):
    # ENTSO-E erwartet UTC: Wien-Mitternacht = UTC-2h (CEST)
    day_start_utc = datetime(local_date.year, local_date.month, local_date.day,
                             0, 0, tzinfo=VIENNA).astimezone(timezone.utc)
    day_end_utc = day_start_utc + timedelta(days=1)
    fmt = lambda d: d.strftime('%Y%m%d%H%M')
    url = (
        f'https://web-api.tp.entsoe.eu/api'
        f'?securityToken={TOKEN}'
        f'&documentType=A44'
        f'&in_Domain={DOMAIN}'
        f'&out_Domain={DOMAIN}'
        f'&periodStart={fmt(day_start_utc)}'
        f'&periodEnd={fmt(day_end_utc)}'
    )
    with urllib.request.urlopen(url) as r:
        xml_text = r.read()
    root = ET.fromstring(xml_text)
    seen = {}
    for ts in root.findall('ns:TimeSeries', NS):
        for period in ts.findall('ns:Period', NS):
            period_start_str = period.find('ns:timeInterval/ns:start', NS).text.strip()
            period_start = datetime.fromisoformat(period_start_str.replace('Z', '+00:00'))
            res = period.find('ns:resolution', NS).text.strip()
            interval_min = 15 if res == 'PT15M' else 60
            for pt in period.findall('ns:Point', NS):
                pos = int(pt.find('ns:position', NS).text.strip()) - 1
                price_mwh = float(pt.find('ns:price.amount', NS).text.strip())
                slot_start = period_start + timedelta(minutes=pos * interval_min)
                slot_end = slot_start + timedelta(minutes=interval_min)
                # nur Slots innerhalb des gewünschten Wien-Tages
                if slot_start < day_start_utc or slot_start >= day_end_utc:
                    continue
                key = slot_start.isoformat()
                if key not in seen:
                    seen[key] = {
                        's': slot_start.isoformat(),
                        'e': slot_end.isoformat(),
                        'p': round(price_mwh / 1000, 6)
                    }
    return sorted(seen.values(), key=lambda x: x['s'])

now_utc = datetime.now(timezone.utc)
now_vienna = now_utc.astimezone(VIENNA)
today_local = now_vienna.date()
tomorrow_local = today_local + timedelta(days=1)

data = {'updated': now_utc.isoformat(), 'today': [], 'tomorrow': []}

try:
    data['today'] = fetch_day(today_local)
    print(f"Heute: {len(data['today'])} Punkte")
except Exception as e:
    print(f"Fehler heute: {e}")

try:
    data['tomorrow'] = fetch_day(tomorrow_local)
    print(f"Morgen: {len(data['tomorrow'])} Punkte")
except Exception as e:
    print(f"Morgen noch nicht verfügbar: {e}")

with open('data.json', 'w') as f:
    json.dump(data, f)

print("data.json gespeichert.")
