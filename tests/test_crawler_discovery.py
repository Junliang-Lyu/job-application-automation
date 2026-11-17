import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import fetch_jobs_browser as fetch

class DiscoveryTests(unittest.TestCase):
    def test_blocked_pages_have_actionable_reasons(self):
        self.assertEqual(fetch.discovery_blocker('正在进行安全验证 由 Cloudflare 提供'), 'security_verification')
        self.assertIsNone(fetch.discovery_blocker('Software Engineer Python APIs'))

    def test_failures_are_not_counted_as_jobs(self):
        result=fetch.discovery_summary([{'status':'needs_human','_source':'Handshake'},
                                         {'status':'fetched','_source':'LinkedIn'}],3.2)
        self.assertEqual(result['fetched'],1)
        self.assertEqual(result['failures'],1)
        self.assertEqual(result['by_source'],{'LinkedIn':1})

    def test_unrelated_platforms_not_silently_dropped(self):
        self.assertEqual(fetch.detect_ats_from_url('https://jobs.lever.co/acme/1'),'Lever')

    def test_fixed_test_companies_are_retired(self):
        import public_ats
        self.assertEqual(public_ats.fetch_configured_boards(),[])

if __name__=='__main__': unittest.main()
