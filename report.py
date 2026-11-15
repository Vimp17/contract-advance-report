"""Dated contractor payment report. Monetary values are integer kopecks."""
import argparse
import csv
from datetime import date
import json
from pathlib import Path
import re
import sys


class DataError(ValueError):
    """Invalid input with a human-readable diagnostic."""


SCHEMAS = {
    'contracts': 'contract_id contractor amount signed_at'.split(),
    'works': 'work_id contract_id title price status completed_at cancelled_at'.split(),
    'payments': 'payment_id contract_id work_id purpose amount status created_at paid_at refunded_at'.split(),
}
MONEY_FIELDS = {'amount', 'price'}
DATE_FIELDS = {'signed_at', 'completed_at', 'cancelled_at', 'created_at', 'paid_at', 'refunded_at'}
POLICY = 'direct-first; done by completed_at/work_id; planned by work_id; cancelled excluded from pool'


def parse_money(value):
    """Parse decimal rubles without rounding, floating point or precision limits."""
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
        raise DataError(f'Некорректная сумма: {value!r}; ожидаются неотрицательные рубли, до 2 знаков')
    rubles, _, fraction = value.partition('.')
    return int(rubles) * 100 + int(fraction.ljust(2, '0'))


def money(kopecks):
    sign = '-' if kopecks < 0 else ''
    whole, fraction = divmod(abs(kopecks), 100)
    return f'{sign}{whole}.{fraction:02d}'


def parse_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise DataError(f'Некорректная дата: {value!r}; ожидается YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f'Некорректная дата: {value!r}') from exc


def load_data(directory):
    """Load and validate the complete snapshot, including future records."""
    data = {}
    for kind, fields in SCHEMAS.items():
        path = Path(directory) / f'{kind}.csv'
        records, seen = [], set()
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None or sorted(reader.fieldnames) != sorted(fields):
                    raise DataError(f'{path.name}: неверные колонки; ожидаются {", ".join(fields)}')
                for line, row in enumerate(reader, 2):
                    try:
                        if None in row or any(v is None for v in row.values()):
                            raise DataError('число значений не совпадает с заголовком')
                        row = {k: v.strip() for k, v in row.items()}
                        id_key = fields[0]
                        if not row[id_key] or row[id_key] in seen:
                            raise DataError(f'пустой или повторный {id_key}: {row[id_key]!r}')
                        seen.add(row[id_key])
                        for key, value in row.items():
                            if key in MONEY_FIELDS:
                                row[key] = parse_money(value)
                            elif key in DATE_FIELDS:
                                row[key] = parse_date(value) if value else None
                            elif not value and key not in {'work_id', 'purpose'}:
                                raise DataError(f'пустое поле {key}')
                        records.append(row)
                    except DataError as exc:
                        raise DataError(f'{path.name}:{line}: {exc}') from exc
        except (OSError, UnicodeError, csv.Error) as exc:
            raise DataError(f'{path.name}: {exc}') from exc
        data[kind] = records
    _validate(data)
    return data


def _validate(data):
    contracts = {r['contract_id']: r for r in data['contracts']}
    works = {r['work_id']: r for r in data['works']}

    def require(condition, message):
        if not condition:
            raise DataError(message)

    for c in contracts.values():
        require(c['signed_at'] is not None, f"contracts.csv {c['contract_id']}: нужна signed_at")
    for w in works.values():
        label = f"works.csv {w['work_id']}"
        require(w['contract_id'] in contracts, f'{label}: неизвестный договор')
        require(w['status'] in {'planned', 'done', 'cancelled'}, f'{label}: неизвестный status')
        require((w['completed_at'] is not None) == (w['status'] == 'done'), f'{label}: completed_at не соответствует status')
        require((w['cancelled_at'] is not None) == (w['status'] == 'cancelled'), f'{label}: cancelled_at не соответствует status')
        for key in ('completed_at', 'cancelled_at'):
            require(w[key] is None or w[key] >= contracts[w['contract_id']]['signed_at'], f'{label}: {key} раньше договора')
    for p in data['payments']:
        label = f"payments.csv {p['payment_id']}"
        require(p['contract_id'] in contracts, f'{label}: неизвестный договор')
        if p['work_id']:
            require(p['work_id'] in works, f'{label}: неизвестная работа')
            require(works[p['work_id']]['contract_id'] == p['contract_id'], f'{label}: работа другого договора')
        require(p['status'] in {'queued', 'paid', 'refunded'}, f'{label}: неизвестный status')
        require(p['created_at'] is not None, f'{label}: нужна created_at')
        require((p['paid_at'] is not None) == (p['status'] in {'paid', 'refunded'}), f'{label}: paid_at не соответствует status')
        require((p['refunded_at'] is not None) == (p['status'] == 'refunded'), f'{label}: refunded_at не соответствует status')
        require(p['created_at'] >= contracts[p['contract_id']]['signed_at'], f'{label}: created_at раньше договора')
        require(p['paid_at'] is None or p['paid_at'] >= p['created_at'], f'{label}: paid_at раньше created_at')
        require(p['refunded_at'] is None or p['refunded_at'] >= p['paid_at'], f'{label}: refunded_at раньше paid_at')


def work_status(work, as_of):
    if work['cancelled_at'] and work['cancelled_at'] <= as_of:
        return 'cancelled'
    if work['completed_at'] and work['completed_at'] <= as_of:
        return 'done'
    return 'planned'


def build_report(data, as_of):
    """Calculate a fresh allocation at end of day; no historical allocation is implied."""
    as_of = parse_date(as_of) if isinstance(as_of, str) else as_of
    result = {'as_of': as_of.isoformat(), 'allocation_policy': POLICY,
              'works': [], 'contracts': [], 'warnings': []}
    for c in sorted(data['contracts'], key=lambda r: r['contract_id']):
        cid = c['contract_id']
        if c['signed_at'] > as_of:
            continue
        works = []
        for w in sorted(data['works'], key=lambda r: r['work_id']):
            if w['contract_id'] != cid:
                continue
            status = work_status(w, as_of)
            works.append({'contract_id': cid, 'work_id': w['work_id'], 'title': w['title'],
                          'status': status, 'price': w['price'],
                          'effective_price': 0 if status == 'cancelled' else w['price'],
                          'direct_paid': 0, 'allocated_advance': 0})
        by_id = {w['work_id']: w for w in works}
        paid = pool = 0
        for p in data['payments']:
            if p['contract_id'] != cid or not p['paid_at'] or p['paid_at'] > as_of:
                continue
            if p['refunded_at'] and p['refunded_at'] <= as_of:
                continue
            paid += p['amount']
            if p['work_id']:
                by_id[p['work_id']]['direct_paid'] += p['amount']
            else:
                pool += p['amount']
        originals = {w['work_id']: w for w in data['works'] if w['contract_id'] == cid}
        priority = sorted((w for w in works if w['status'] != 'cancelled'), key=lambda w: (
            0 if w['status'] == 'done' else 1,
            originals[w['work_id']]['completed_at'] if w['status'] == 'done' else date.max,
            w['work_id']))
        for w in priority:
            allocation = min(pool, max(0, w['effective_price'] - w['direct_paid']))
            w['allocated_advance'] = allocation
            pool -= allocation
        for w in works:
            w['paid'] = w['direct_paid'] + w['allocated_advance']
            w['remaining'] = max(0, w['effective_price'] - w['paid'])
            w['overpaid'] = max(0, w['paid'] - w['effective_price'])
            if w['overpaid']:
                result['warnings'].append(f"{cid}/{w['work_id']}: платёж сверх действующей цены {money(w['overpaid'])}")
        active_price = sum(w['effective_price'] for w in works)
        if active_price != c['amount']:
            result['warnings'].append(f'{cid}: сумма действующих работ {money(active_price)} отличается от договора {money(c["amount"])}')
        if pool:
            result['warnings'].append(f'{cid}: нераспределённый аванс {money(pool)}')
        result['works'].extend(works)
        result['contracts'].append({'contract_id': cid, 'contractor': c['contractor'],
            'amount': c['amount'], 'active_price': active_price, 'paid': paid,
            'allocated': paid - pool, 'unallocated': pool,
            'remaining': sum(w['remaining'] for w in works),
            'overpaid': sum(w['overpaid'] for w in works), 'contract_balance': c['amount'] - paid})
    return result


WORK_COLUMNS = 'contract_id work_id title status price effective_price direct_paid allocated_advance paid remaining overpaid'.split()
CONTRACT_COLUMNS = 'contract_id contractor amount active_price paid allocated unallocated remaining overpaid contract_balance'.split()
REPORT_MONEY = set(WORK_COLUMNS[4:] + CONTRACT_COLUMNS[2:])


def formatted_report(report):
    return {**report, **{kind: [{k: money(v) if k in REPORT_MONEY else v for k, v in row.items()}
                               for row in report[kind]] for kind in ('works', 'contracts')}}


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    formatted = formatted_report(report)
    for kind, fields in [('works', WORK_COLUMNS), ('contracts', CONTRACT_COLUMNS)]:
        with (directory / f'{kind}.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['as_of'] + fields)
            writer.writeheader()
            writer.writerows({'as_of': report['as_of'], **row} for row in formatted[kind])
    (directory / 'report.json').write_text(json.dumps(formatted, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Отчёт по авансам и работам на конец указанной даты')
    parser.add_argument('--date', required=True, help='Дата включительно, YYYY-MM-DD')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data')
    parser.add_argument('--output-dir', type=Path, default=Path('reports'))
    args = parser.parse_args(argv)
    try:
        as_of = parse_date(args.date)
        # Prevent overwriting any input file, including through symbolic links.
        if args.data_dir.resolve() == args.output_dir.resolve():
            raise DataError('Папки входных данных и отчёта должны различаться')
        report = build_report(load_data(args.data_dir), as_of)
        write_report(report, args.output_dir)
    except (DataError, OSError) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        return 2
    print(f'Отчёт на {report["as_of"]}: {args.output_dir.resolve()}')
    for warning in report['warnings']:
        print(f'Внимание: {warning}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
