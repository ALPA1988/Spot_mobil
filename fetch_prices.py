import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ.get('ENTSOE_TOKEN')
if not TOKEN:
    raise RuntimeError('ENTSOE_TOKEN environment variable is not set')

DOMAIN = '10YAT-APG------L'
NS = {'ns': 'urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3'}
VIENNA = ZoneInfo('Europe/Vienna')


def classification_position(time_series):
    node = time_series.find(
        'ns:classificationSequence_AttributeInstanceComponent.position', NS
    )
    return node.text.strip() if node is not None and node.text else None


def fetch_day(local_date):
    day_start_local = datetime.combine(local_date, time.min, tzinfo=VIENNA)
    day_end_local = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=VIENNA)
    day_start_utc = day_start_local.astimezone(timezone.utc)
    day_end_utc = day_end_local.astimezone(timezone.utc)

    params = urllib.parse.urlencode({
        'securityToken': TOKEN,
        'documentType': 'A44',
        'in_Domain': DOMAIN,
        'out_Domain': DOMAIN,
        'periodStart': day_start_utc.strftime('%Y%m%d%H%M'),
        'periodEnd': day_end_utc.strftime('%Y%m%d%H%M'),
    })
    url = f'https://web-api.tp.entsoe.eu/api?{params}'

    with urllib.request.urlopen(url) as response:
        root = ET.fromstring(response.read())

    all_series = root.findall('ns:TimeSeries', NS)
    position_one = [ts for ts in all_series if classification_position(ts) == '1']
    if position_one:
        selected_series = position_one
    elif len(all_series) == 1:
        selected_series = all_series
    else:
        positions = sorted({classification_position(ts) for ts in all_series}, key=str)
        raise ValueError(f'No unique ENTSO-E price series at position 1; positions={positions}')

    seen = {}
    for time_series in selected_series:
        currency = time_series.findtext('ns:currency_Unit.name', namespaces=NS)
        price_unit = time_series.findtext('ns:price_Measure_Unit.name', namespaces=NS)
        if currency != 'EUR' or price_unit != 'MWH':
            raise ValueError(f'Unexpected price unit: {currency}/{price_unit}')

        for period in time_series.findall('ns:Period', NS):
            period_start_str = period.findtext('ns:timeInterval/ns:start', namespaces=NS)
            period_start = datetime.fromisoformat(period_start_str.strip().replace('Z', '+00:00'))
            resolution = period.findtext('ns:resolution', namespaces=NS)
            if resolution != 'PT15M':
                raise ValueError(f'Unexpected ENTSO-E resolution: {resolution}')

            for point in period.findall('ns:Point', NS):
                position = int(point.findtext('ns:position', namespaces=NS)) - 1
                price_mwh = float(point.findtext('ns:price.amount', namespaces=NS))
                slot_start = period_start + timedelta(minutes=position * 15)
                slot_end = slot_start + timedelta(minutes=15)
                if slot_start < day_start_utc or slot_start >= day_end_utc:
                    continue

                seen[slot_start.isoformat()] = {
                    's': slot_start.isoformat(),
                    'e': slot_end.isoformat(),
                    'p': round(price_mwh / 1000, 6),
                }

    expected_slots = int((day_end_utc - day_start_utc).total_seconds() // (15 * 60))
    if len(seen) != expected_slots:
        raise ValueError(
            f'Incomplete ENTSO-E position-1 series for {local_date}: '
            f'expected {expected_slots} slots, got {len(seen)}'
        )

    return sorted(seen.values(), key=lambda item: item['s'])


def main():
    now_utc = datetime.now(timezone.utc)
    today_local = now_utc.astimezone(VIENNA).date()
    tomorrow_local = today_local + timedelta(days=1)

    today = fetch_day(today_local)
    print(f'Heute: {len(today)} Punkte')

    try:
        tomorrow = fetch_day(tomorrow_local)
        print(f'Morgen: {len(tomorrow)} Punkte')
    except Exception as error:
        tomorrow = []
        print(f'Morgen noch nicht verfügbar: {error}')

    data = {
        'updated': now_utc.isoformat(),
        'today': today,
        'tomorrow': tomorrow,
    }
    with open('data.json', 'w', encoding='utf-8') as file:
        json.dump(data, file)

    print('data.json gespeichert.')


if __name__ == '__main__':
    main()
