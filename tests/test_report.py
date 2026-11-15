import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from report import DataError, build_report, load_data, money, parse_money


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.data = load_data(ROOT / 'data')

    def report(self, day='2026-11-10', data=None):
        return build_report(self.data if data is None else data, day)

    def test_reference_contracts(self):
        r = self.report()
        d1, d2 = r['contracts']
        self.assertEqual((d1['paid'], d1['active_price'], d1['remaining'],
                          d1['contract_balance'], d1['unallocated']),
                         (31500000, 50000000, 18500000, 28500000, 0))
        self.assertEqual((d2['paid'], d2['remaining']), (7500000, 1500000))

    def test_reference_works_and_direct_payment_priority(self):
        works = {w['work_id']: w for w in self.report()['works']}
        self.assertEqual([works[f'W-{i}']['paid'] for i in range(11, 17)],
                         [10000000, 10000000, 0, 10000000, 1500000, 0])
        self.assertEqual(works['W-14']['direct_paid'], 1500000)
        self.assertEqual(works['W-13']['effective_price'], 0)
        self.assertEqual(works['W-23']['status'], 'planned')
        self.assertEqual(works['W-23']['paid'], 2500000)

    def test_refund_boundary(self):
        self.assertEqual(self.report('2026-09-01')['contracts'][0]['paid'], 30000000)
        self.assertEqual(self.report('2026-09-02')['contracts'][0]['paid'], 35000000)
        self.assertEqual(self.report('2026-09-09')['contracts'][0]['paid'], 35000000)
        self.assertEqual(self.report('2026-09-10')['contracts'][0]['paid'], 30000000)

    def test_acceptance_and_cancellation_boundaries(self):
        for day, state in [('2026-11-10', 'planned'), ('2026-11-11', 'done')]:
            self.assertEqual(self.report(day)['works'][-1]['status'], state)
        for day, state in [('2026-10-11', 'planned'), ('2026-10-12', 'cancelled')]:
            self.assertEqual(self.report(day)['works'][2]['status'], state)

    def test_future_and_queued_payments(self):
        self.assertEqual(self.report('2026-11-11')['contracts'][1]['paid'], 7500000)
        d2 = self.report('2026-11-12')['contracts'][1]
        self.assertEqual((d2['paid'], d2['unallocated'], d2['remaining']), (9500000, 500000, 0))
        self.assertEqual(self.report('2026-12-01')['contracts'][0]['paid'], 31500000)

    def test_unsigned_contracts_excluded(self):
        self.assertEqual(self.report('2026-08-24')['contracts'], [])
        self.assertEqual(self.report('2026-08-24')['works'], [])

    def test_money_is_exact_and_strict(self):
        self.assertEqual(parse_money('0.10') + parse_money('0.20'), 30)
        self.assertEqual(money(123456789012345678901), '1234567890123456789.01')
        for bad in ['NaN', 'Infinity', '1.001', '-1', '1e2', '', '1,25']:
            with self.subTest(bad=bad), self.assertRaises(DataError):
                parse_money(bad)

    def test_conservation_and_nonnegative_balances_across_dates(self):
        for day in ['2026-08-25', '2026-09-02', '2026-10-11', '2026-10-12', '2026-11-10', '2026-11-12']:
            r = self.report(day)
            for c in r['contracts']:
                works = [w for w in r['works'] if w['contract_id'] == c['contract_id']]
                self.assertEqual(sum(w['paid'] for w in works) + c['unallocated'], c['paid'])
                self.assertEqual(sum(w['remaining'] for w in works), c['remaining'])
                self.assertTrue(all(w['remaining'] >= 0 for w in works))

    def test_row_order_does_not_change_report(self):
        shuffled = {key: list(reversed(rows)) for key, rows in self.data.items()}
        self.assertEqual(self.report(), self.report(data=shuffled))

    def modified_data(self, filename, mutate):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        dest = Path(temp.name)
        for source in (ROOT / 'data').glob('*.csv'):
            with source.open(newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fields, rows = reader.fieldnames, list(reader)
            if source.name == filename:
                mutate(rows)
            with (dest / source.name).open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        return dest

    def test_invalid_data_is_rejected_with_context(self):
        cases = [
            ('works.csv', lambda r: r.append(r[0].copy())),
            ('works.csv', lambda r: r[0].update(contract_id='missing')),
            ('works.csv', lambda r: r[0].update(completed_at='')),
            ('works.csv', lambda r: r[0].update(status='unknown')),
            ('works.csv', lambda r: r[0].update(price='1.001')),
            ('payments.csv', lambda r: r[2].update(work_id='W-21')),
            ('payments.csv', lambda r: r[0].update(paid_at='2026-08-01')),
            ('payments.csv', lambda r: r[1].update(refunded_at='2026-09-01')),
            ('payments.csv', lambda r: r[3].update(paid_at='2026-11-01')),
            ('contracts.csv', lambda r: r[0].update(signed_at='2026-99-99')),
        ]
        for filename, mutate in cases:
            with self.subTest(filename=filename), self.assertRaisesRegex(DataError, filename):
                load_data(self.modified_data(filename, mutate))

    def test_direct_overpayment_and_cancelled_work_are_not_lost(self):
        for work_id, overpaid in [('W-14', 1000000), ('W-13', 11000000)]:
            path = self.modified_data('payments.csv', lambda r: r[2].update(work_id=work_id, amount='110000.00'))
            r = self.report(data=load_data(path))
            work = next(w for w in r['works'] if w['work_id'] == work_id)
            self.assertEqual((work['paid'], work['remaining'], work['overpaid']), (11000000, 0, overpaid))
            c = r['contracts'][0]
            self.assertEqual(sum(w['paid'] for w in r['works'] if w['contract_id'] == 'D-1') + c['unallocated'], c['paid'])

    def test_cli_outputs_and_invalid_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run([sys.executable, str(ROOT / 'report.py'), '--date', '2026-11-10',
                                  '--data-dir', str(ROOT / 'data'), '--output-dir', tmp], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads((Path(tmp) / 'report.json').read_text())
            self.assertEqual(result['contracts'][0]['paid'], '315000.00')
            with (Path(tmp) / 'works.csv').open(encoding='utf-8-sig', newline='') as f:
                self.assertEqual(len(list(csv.DictReader(f))), 9)
            self.assertTrue((Path(tmp) / 'contracts.csv').exists())
        run = subprocess.run([sys.executable, str(ROOT / 'report.py'), '--date', 'bad'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertNotIn('Traceback', run.stderr)


if __name__ == '__main__':
    unittest.main()
