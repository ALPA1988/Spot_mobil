import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
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


def fetch_slots(start_local_date, end_local_date):
    start_local = datetime.combine(start_local_date, time.min, tzinfo=VIENNA)
    end_local = datetime.combine(end_local_date, time.min, tzinfo=VIENNA)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    params = urllib.parse.urlencode({
        'securityToken': TOKEN,
        'documentType': 'A44',
        'in_Domain': DOMAIN,
        'out_Domain': DOMAIN,
        'periodStart': start_utc.strftime('%Y%m%d%H%M'),
        'periodEnd': end_utc.strftime('%Y%m%d%H%M'),
    })
    url = f'https://web-api.tp.entsoe.eu/api?{params}'

    try:
        with urllib.request.urlopen(url) as response:
            root = ET.fromstring(response.read())
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ENTSO-E API HTTP {error.code}: {details}") from error

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
                if slot_start < start_utc or slot_start >= end_utc:
                    continue

                seen[slot_start.isoformat()] = {
                    's': slot_start.isoformat(),
                    'e': slot_end.isoformat(),
                    'p': round(price_mwh / 1000, 6),
                }

    expected_slots = int((end_utc - start_utc).total_seconds() // (15 * 60))
    if len(seen) != expected_slots:
        raise ValueError(
            f'Incomplete ENTSO-E position-1 series for {start_local_date}..{end_local_date}: '
            f'expected {expected_slots} slots, got {len(seen)}'
        )

    return sorted(seen.values(), key=lambda item: item['s'])


def fetch_day(local_date):
    return fetch_slots(local_date, local_date + timedelta(days=1))


def aggregate_daily(slots):
    by_day = defaultdict(list)
    for slot in slots:
        local_day = datetime.fromisoformat(slot['s']).astimezone(VIENNA).date()
        by_day[local_day].append(slot['p'])

    return [
        {
            'd': day.isoformat(),
            'min': round(min(prices), 6),
            'avg': round(sum(prices) / len(prices), 6),
            'max': round(max(prices), 6),
        }
        for day, prices in sorted(by_day.items())
    ]


def main():
    now_utc = datetime.now(timezone.utc)
    today_local = now_utc.astimezone(VIENNA).date()
    tomorrow_local = today_local + timedelta(days=1)
    history_start = today_local - timedelta(days=29)

    history_slots = fetch_slots(history_start, tomorrow_local)
    history = aggregate_daily(history_slots)
    today = [
        slot for slot in history_slots
        if datetime.fromisoformat(slot['s']).astimezone(VIENNA).date() == today_local
    ]
    print(f'Heute: {len(today)} Punkte')
    print(f'Historie: {len(history)} Tage')

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
        'history': history,
    }
    with open('data.json', 'w', encoding='utf-8') as file:
        json.dump(data, file)

    print('data.json gespeichert.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=ENTSO-E fetch failed::{message}")
        raise
