"""Gmail reconciliation with a fake mailbox (no network)."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gmail_reconcile as g  # noqa: E402
from manual_workbench import Workbench, FIELDS  # noqa: E402


def msg(sender, subject, body='', day='2026-10-20', mid='m'):
    return dict(id=mid, date=day, sender=sender, subject=subject, body=body)


class FakeGmail:
    def __init__(self, messages):
        self.messages = {m['id']: m for m in messages}
        self.queries = []

    def search(self, query, limit=300):
        self.queries.append(query)
        return list(self.messages)

    def message(self, mid):
        return self.messages[mid]


class FlakyGmail(FakeGmail):
    def __init__(self, messages, broken):
        super().__init__(messages)
        self.broken = set(broken)

    def message(self, mid):
        if mid in self.broken:
            raise g.GmailError(403, 'forbidden', 'Not allowed for this message')
        return super().message(mid)


class ErrorHandlingTests(unittest.TestCase):
    def test_http_error_carries_google_reason_and_retries_rate_limits(self):
        import io as _io, urllib.error
        calls = []

        def fake_urlopen(req, timeout=30):
            calls.append(1)
            body = json.dumps({'error': {'message': 'Rate limit', 'errors': [{'reason': 'rateLimitExceeded'}]}}) if len(calls) < 3 \
                else json.dumps({'error': {'message': 'Request had insufficient authentication scopes.', 'status': 'PERMISSION_DENIED'}})
            raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, _io.BytesIO(body.encode()))
        orig_open, orig_sleep = g.urllib.request.urlopen, g.time.sleep
        g.urllib.request.urlopen, g.time.sleep = fake_urlopen, lambda s: None
        try:
            with self.assertRaises(g.GmailError) as ctx:
                g.Gmail('t').get('messages/1')
        finally:
            g.urllib.request.urlopen, g.time.sleep = orig_open, orig_sleep
        self.assertEqual(len(calls), 3)              # two rate-limit retries, then the real error
        self.assertIn('insufficient authentication scopes', str(ctx.exception))
        self.assertEqual(ctx.exception.code, 403)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.started = []
        self.orig = (g.start_background, g.config_dir, dict(g.STATUS))
        g.start_background = lambda wb: self.started.append(wb)
        self.tmp = tempfile.TemporaryDirectory()
        g.config_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        g.start_background, g.config_dir = self.orig[0], self.orig[1]
        g.STATUS.clear(); g.STATUS.update(self.orig[2])
        self.tmp.cleanup()

    def test_new_authorization_is_picked_up_without_restart(self):
        g.STATUS.update(state='needs_auth')
        g.maybe_refresh('wb')
        self.assertEqual(self.started, [])           # still no token
        (Path(self.tmp.name) / 'gmail-token.json').write_text('{}')
        g.maybe_refresh('wb')
        self.assertEqual(self.started, ['wb'])

    def test_hourly_recheck_only_when_stale(self):
        import time as _t
        g.STATUS.update(state='done', finished=_t.time())
        g.maybe_refresh('wb')
        self.assertEqual(self.started, [])
        g.STATUS['finished'] = _t.time() - 3700
        g.maybe_refresh('wb')
        self.assertEqual(self.started, ['wb'])


class ClassifyTests(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(g.classify(msg('Affirm <no-reply@affirm.com>', 'Thank you for applying to Affirm',
                         'If your background matches, we will reach out to schedule an interview.')), 'confirmed')
        self.assertEqual(g.classify(msg('x@y.com', 'Your application', 'Thank you for applying. Unfortunately, after careful review '
                         'we have decided not to move forward with your application.')), 'rejected')
        self.assertEqual(g.classify(msg('x@y.com', 'Next steps', 'Please complete the HackerRank assessment within 7 days.')), 'oa')
        self.assertEqual(g.classify(msg('noreply@codesignal.com', 'Invitation', '')), 'oa')
        self.assertEqual(g.classify(msg('SAJA <noreply@mail.saja.com>', 'Round one: your SAJA interview challenge', '')), 'oa')
        self.assertEqual(g.classify(msg('x@y.com', 'Interview', 'We would like to invite you to a 30 minute phone screen. '
                         'Please select a time that works.')), 'interview')
        self.assertIsNone(g.classify(msg('jobs-noreply@linkedin.com', 'New jobs for you', 'Software Engineer at Acme')))

    def test_company_must_be_in_sender_or_subject(self):
        c = [dict(kind='csv', key='k1', company='Precisely', title='Associate Software Engineer', since='2026-10-01')]
        self.assertEqual(g.match(msg('HR <hr@other.com>', 'Update', 'We will precisely follow up'), c)[0], [])
        self.assertEqual(len(g.match(msg('Precisely Talent <talent@precisely.com>', 'Your application'), c)[0]), 1)
        self.assertEqual(len(g.match(msg('no-reply@hire.lever.co', 'Thank you for applying to Precisely'), c)[0]), 1)

    def test_company_variants(self):
        for company, sender, subject in [('H&R Block', 'H&R Block Careers <careers@hrblock.com>', 'Application received'),
                                         ('M3 USA', 'M3USA <no-reply@smartrecruiters.com>', 'Thanks'),
                                         ('Jobright.ai', 'Jobright AI <team@x.com>', 'Thanks'),
                                         ('Affirm', 'Careers <careers@affirm.com>', 'Thanks'),
                                         ('Plaid Technologies', 'Plaid <no-reply@greenhouse-mail.io>', 'Thanks'),
                                         ("Cincinnati Children's", 'Recruiting', "Your application to Cincinnati Children's")]:
            c = [dict(kind='csv', key='k', company=company, title='Engineer', since='')]
            self.assertEqual(len(g.match(msg(sender, subject), c)[0]), 1, company)

    def test_repost_goes_to_latest_application_before_email(self):
        c = [dict(kind='csv', key='old', company='Precisely', title='Associate software Engineer (Python AI)', since='2026-10-11'),
             dict(kind='csv', key='new', company='Precisely', title='Associate software Engineer (Python AI)', since='2026-10-25')]
        self.assertEqual(g.match(msg('Precisely <hr@precisely.com>', 'Update', day='2026-10-27'), c)[0][0]['key'], 'new')
        self.assertEqual(g.match(msg('Precisely <hr@precisely.com>', 'Update', day='2026-10-20'), c)[0][0]['key'], 'old')

    def test_email_before_application_ignored(self):
        c = [dict(kind='csv', key='k', company='Affirm', title='Engineer', since='2026-10-25')]
        self.assertEqual(g.match(msg('Affirm <a@affirm.com>', 'Thanks', day='2026-10-01'), c)[0], [])

    def test_multiple_roles_resolved_by_title_or_sent_to_review(self):
        c = [dict(kind='csv', key='a', company='BeaconFire', title='Backend Software Engineer', since=''),
             dict(kind='csv', key='b', company='BeaconFire', title='Data Engineer', since='')]
        hits, why = g.match(msg('BeaconFire <hr@beaconfire.com>', 'Your application for Data Engineer'), c)
        self.assertEqual([h['key'] for h in hits], ['b'])
        hits, why = g.match(msg('BeaconFire <hr@beaconfire.com>', 'Your application'), c)
        self.assertEqual((len(hits), why), (2, 'multiple_roles'))


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'applications').mkdir()
        (self.root / 'profile.md').write_text('Primary Application Email: me@gmail.com (Gmail)\n', encoding='utf-8')
        rows = [dict(Company='Affirm', Position='Software Engineer I', Status='Applied', Platform='LinkedIn',
                     **{'Date Applied': '2026-10-12', 'Job URL': 'https://www.linkedin.com/jobs/view/1/'}),
                dict(Company='BeaconFire', Position='Backend Software Engineer', Status='Applied', Platform='Handshake',
                     **{'Date Applied': '2026-10-14', 'Job URL': 'https://smu.joinhandshake.com/jobs/2'}),
                dict(Company='BeaconFire', Position='Data Engineer', Status='Applied', Platform='Handshake',
                     **{'Date Applied': '2026-10-12', 'Job URL': 'https://smu.joinhandshake.com/jobs/3'}),
                dict(Company='Joint Strategic Technologies', Position='AI Engineer', Status='Submission Unknown', Platform='LinkedIn',
                     **{'Date Applied': '2026-10-14', 'Job URL': 'https://www.linkedin.com/jobs/view/4/'})]
        with open(self.root / 'applications/applications.csv', 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator='\r\n')
            w.writeheader()
            for r in rows:
                w.writerow({**dict.fromkeys(FIELDS, ''), **r})
        (self.root / 'tmp/batch-20261015').mkdir(parents=True)
        (self.root / 'tmp/batch-20261015/queue.json').write_text(json.dumps([dict(
            sourceUrl='https://www.linkedin.com/jobs/view/9/', title='Software Engineering, New Grad', company='Plaid Technologies',
            status='submission_unknown', analysis=dict(fit_score=88, worth_applying=True))]), encoding='utf-8')
        self.wb = Workbench(self.root, [], discover=True)
        self.mail = FakeGmail([
            msg('Plaid <no-reply@greenhouse-mail.io>', 'Thank you for applying to Plaid', 'We received your application.', '2026-10-16', 'p1'),
            msg('Affirm <careers@affirm.com>', 'Update on your application', 'Unfortunately, we have decided to move forward with other candidates.', '2026-10-20', 'a1'),
            msg('BeaconFire <hr@beaconfire.com>', 'Next steps', 'Please complete the HackerRank assessment.', '2026-10-21', 'b1'),
            msg('Joint Strategic Technologies <jobs@jst.com>', 'Application received', 'Thank you for your application.', '2026-10-15', 'j1'),
            msg('LinkedIn <jobs-noreply@linkedin.com>', 'Jobs you may like', 'Engineer at Acme', '2026-10-22', 'n1'),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with open(self.root / 'applications/applications.csv', encoding='utf-8-sig') as f:
            return {r['Job URL']: r for r in csv.DictReader(f)}

    def test_dry_run_writes_nothing(self):
        before = (self.root / 'applications/applications.csv').read_bytes()
        s = g.reconcile(self.root, self.wb, gmail=self.mail, dry_run=True)
        self.assertIn('Plaid Technologies · Software Engineering, New Grad', s['confirmed'])
        self.assertEqual((self.root / 'applications/applications.csv').read_bytes(), before)
        self.assertFalse((self.root / 'applications/email-events.local.jsonl').exists())

    def test_full_run(self):
        s = g.reconcile(self.root, self.wb, gmail=self.mail)
        rows = self.rows()
        self.assertEqual(rows['https://www.linkedin.com/jobs/view/1/']['Outcome'], '拒')
        self.assertEqual(rows['https://www.linkedin.com/jobs/view/1/']['Last Contact'], '2026-10-20')
        self.assertEqual(rows['https://www.linkedin.com/jobs/view/4/']['Status'], 'Applied')   # unknown CSV row confirmed
        plaid = rows['https://www.linkedin.com/jobs/view/9/']                                   # unknown queue item confirmed
        self.assertEqual(plaid['Status'], 'Applied')
        self.assertTrue(plaid['Notes'].startswith('Gmail-verified'))
        # Two BeaconFire roles and no title in the email -> review, nothing written.
        self.assertEqual(rows['https://smu.joinhandshake.com/jobs/2']['Outcome'], '')
        self.assertEqual(len(s['review']), 1)
        journal = (self.root / 'applications/manual-workbench.local.jsonl').read_text(encoding='utf-8')
        self.assertIn('"verification": "gmail"', journal)
        st = g.stats(self.root / 'applications/applications.csv', {'linkedin:9': 88})
        self.assertEqual((st['applied'], st['replied']), (5, 1))
        self.assertEqual(st['by_score']['85+']['applied'], 1)
        # Second run is incremental: nothing re-read, nothing duplicated.
        again = g.reconcile(self.root, self.wb, gmail=self.mail)
        self.assertEqual(again['read'], 0)
        self.assertEqual(len(self.rows()), 5)
        info = self.wb.email_info()
        self.assertEqual(len(info['review']), 1)
        self.wb.dismiss_review([info['review'][0]['id']])
        self.assertEqual(self.wb.email_info()['review'], [])

    def test_one_unreadable_message_is_skipped_not_fatal(self):
        mail = FlakyGmail(list(self.mail.messages.values()), {'n1'})
        s = g.reconcile(self.root, self.wb, gmail=mail)
        self.assertEqual(len(s['skipped']), 1)
        self.assertEqual(self.rows()['https://www.linkedin.com/jobs/view/1/']['Outcome'], '拒')
        seen = (self.root / 'applications/email-events.local.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('"n1"', seen)               # retried next run

    def test_all_messages_unreadable_reports_reason(self):
        msgs = [msg('A <a@x.com>', 'Application', mid=f'x{i}') for i in range(6)]
        mail = FlakyGmail(msgs, {m['id'] for m in msgs})
        with self.assertRaises(g.GmailError):
            g.reconcile(self.root, self.wb, gmail=mail)

    def test_confirmation_for_already_applied_roles_is_not_sent_to_review(self):
        mail = FakeGmail([msg('BeaconFire <hr@beaconfire.com>', 'Thank you for applying', 'We received your application.', '2026-10-22', 'c1')])
        s = g.reconcile(self.root, self.wb, gmail=mail)
        self.assertEqual(s['review'], [])

    def test_query_is_bounded_and_readonly(self):
        g.reconcile(self.root, self.wb, gmail=self.mail, dry_run=True)
        q = self.mail.queries[0]
        self.assertTrue(q.startswith('after:2026/10/11'))
        self.assertIn('-in:sent', q)
        self.assertEqual(g.SCOPE, 'https://www.googleapis.com/auth/gmail.readonly')


if __name__ == '__main__':
    unittest.main()
