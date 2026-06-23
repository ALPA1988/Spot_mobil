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


def fetch_slots(start_local_date, end_local_date, validate=True):
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

            period_end_str = period.findtext('ns:timeInterval/ns:end', namespaces=NS)
            period_end = datetime.fromisoformat(period_end_str.strip().replace('Z', '+00:00'))
            period_slots = int((period_end - period_start).total_seconds() // (15 * 60))
            points = sorted(
                (
                    int(point.findtext('ns:position', namespaces=NS)) - 1,
                    float(point.findtext('ns:price.amount', namespaces=NS)),
                )
                for point in period.findall('ns:Point', NS)
            )

            for index, (position, price_mwh) in enumerate(points):
                next_position = points[index + 1][0] if index + 1 < len(points) else period_slots
                for slot_position in range(position, next_position):
                    slot_start = period_start + timedelta(minutes=slot_position * 15)
                    slot_end = slot_start + timedelta(minutes=15)
                    if slot_start < start_utc or slot_start >= end_utc:
                        continue

                    seen[slot_start.isoformat()] = {
                        's': slot_start.isoformat(),
                        'e': slot_end.isoformat(),
                        'p': round(price_mwh / 1000, 6),
                    }

    expected_slots = int((end_utc - start_utc).total_seconds() // (15 * 60))
    if validate and len(seen) != expected_slots:
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


def local_day_slots(slots, local_date):
    return [
        slot for slot in slots
        if datetime.fromisoformat(slot['s']).astimezone(VIENNA).date() == local_date
    ]


def hourly_average_slots(slots):
    by_hour = defaultdict(list)
    for slot in slots:
        local_start = datetime.fromisoformat(slot['s']).astimezone(VIENNA)
        hour_start = local_start.replace(minute=0, second=0, microsecond=0)
        by_hour[hour_start].append(slot['p'])

    hourly_price = {
        hour_start: round(sum(prices) / len(prices), 6)
        for hour_start, prices in by_hour.items()
    }

    normalized = []
    for slot in slots:
        local_start = datetime.fromisoformat(slot['s']).astimezone(VIENNA)
        hour_start = local_start.replace(minute=0, second=0, microsecond=0)
        normalized.append({**slot, 'p': hourly_price[hour_start]})
    return normalized


def main():
    now_utc = datetime.now(timezone.utc)
    today_local = now_utc.astimezone(VIENNA).date()
    tomorrow_local = today_local + timedelta(days=1)
    day_after_tomorrow = tomorrow_local + timedelta(days=1)
    history_start = today_local - timedelta(days=29)

    all_slots = fetch_slots(history_start, day_after_tomorrow, validate=False)
    slots_by_start = {slot["s"]: slot for slot in all_slots}
    day = history_start
    expected_history_slots = 0
    tomorrow_available = True
    while day < day_after_tomorrow:
        day_slots = [
            slot for slot in slots_by_start.values()
            if datetime.fromisoformat(slot["s"]).astimezone(VIENNA).date() == day
        ]
        day_start = datetime.combine(day, time.min, tzinfo=VIENNA)
        day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=VIENNA)
        expected_day_slots = int(
            (day_end.astimezone(timezone.utc) - day_start.astimezone(timezone.utc)).total_seconds()
            // (15 * 60)
        )
        if len(day_slots) != expected_day_slots:
            if day == tomorrow_local:
                tomorrow_available = False
                print(f"Morgen noch nicht vollständig: {len(day_slots)}/{expected_day_slots} Punkte")
            else:
                print(
                    f"Historie {day}: {len(day_slots)}/{expected_day_slots} Punkte, lade Tag nach"
                )
                for slot in fetch_day(day):
                    slots_by_start[slot["s"]] = slot
        if day < tomorrow_local:
            expected_history_slots += expected_day_slots
        day += timedelta(days=1)

    history_slots = sorted(
        (
            slot for slot in slots_by_start.values()
            if datetime.fromisoformat(slot["s"]).astimezone(VIENNA).date() < tomorrow_local
        ),
        key=lambda item: item["s"],
    )
    if len(history_slots) != expected_history_slots:
        raise ValueError(
            f"Incomplete ENTSO-E history: expected {expected_history_slots} slots, "
            f"got {len(history_slots)}"
        )
    history = aggregate_daily(history_slots)
    today = hourly_average_slots(local_day_slots(history_slots, today_local))
    print(f'Heute: {len(today)} Punkte')
    print(f'Historie: {len(history)} Tage')

    if tomorrow_available:
        tomorrow = hourly_average_slots(local_day_slots(slots_by_start.values(), tomorrow_local))
        print(f'Morgen: {len(tomorrow)} Punkte')
    else:
        tomorrow = []

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
