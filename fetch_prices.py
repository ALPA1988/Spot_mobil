import urllib.request
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone, timedelta

TOKEN = '930c01dc-e1bc-43e8-9f32-30c741099051'
DOMAIN = '10YAT-APG------L'
NS = {'ns': 'urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3'}

def fetch_day(date_utc_start):
    date_utc_end = date_utc_start + timedelta(days=1)
    fmt = lambda d: d.strftime('%Y%m%d%H%M')
    url = (
        f'https://web-api.tp.entsoe.eu/api'
        f'?securityToken={TOKEN}'
        f'&documentType=A44'
        f'&in_Domain={DOMAIN}'
        f'&out_Domain={DOMAIN}'
        f'&periodStart={fmt(date_utc_start)}'
        f'&periodEnd={fmt(date_utc_end)}'
    )
    with urllib.request.urlopen(url) as r:
        xml_text = r.read()
    root = ET.fromstring(xml_text)
    seen = {}  # deduplizieren nach Slot-Start
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
                key = slot_start.isoformat()
                if key not in seen:  # ersten Eintrag pro Slot behalten
                    seen[key] = {
                        's': slot_start.isoformat(),
                        'e': slot_end.isoformat(),
                        'p': round(price_mwh / 1000, 6)
                    }
    return sorted(seen.values(), key=lambda x: x['s'])

now_utc = datetime.now(timezone.utc)
today_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
tomorrow_utc = today_utc + timedelta(days=1)

data = {'updated': now_utc.isoformat(), 'today': [], 'tomorrow': []}

try:
    data['today'] = fetch_day(today_utc)
    print(f"Heute: {len(data['today'])} Punkte")
except Exception as e:
    print(f"Fehler heute: {e}")

try:
    data['tomorrow'] = fetch_day(tomorrow_utc)
    print(f"Morgen: {len(data['tomorrow'])} Punkte")
except Exception as e:
    print(f"Morgen noch nicht verfügbar: {e}")

with open('data.json', 'w') as f:
    json.dump(data, f)

print("data.json gespeichert.")
