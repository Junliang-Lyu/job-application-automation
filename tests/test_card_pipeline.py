import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import card_jds

class CardPipelineTests(unittest.TestCase):
    def test_description_handles_empty_attributes_and_excludes_script(self):
        html='<div class></div><div class="description__text"><p>Build APIs.</p><script>secret</script><p>No sponsorship.</p></div><footer>Not JD</footer>'
        text=card_jds.description_text(html)
        self.assertIn('Build APIs.',text)
        self.assertIn('No sponsorship.',text)
        self.assertNotIn('secret',text)
        self.assertNotIn('Not JD',text)

    def test_applied_duplicates_and_checkpoint_do_not_trigger_requests(self):
        rows=[{'sourceUrl':'https://www.linkedin.com/jobs/view/123/?x=a'},
              {'sourceUrl':'https://www.linkedin.com/jobs/view/123/?x=b'},
              {'sourceUrl':'https://www.linkedin.com/jobs/view/456/'}]
        prior=[{**rows[0],'status':'fetched','text':'cached'}]
        def unexpected(_): raise AssertionError('No request expected')
        result,stats=card_jds.fetch_cards(rows,prior=prior,excluded=[rows[2]['sourceUrl']],fetch=unexpected)
        self.assertEqual(len(result),1)
        self.assertEqual(stats['requested'],0)
        self.assertEqual(stats['reused'],1)

    def test_single_failure_preserves_other_jobs(self):
        rows=[{'sourceUrl':f'https://www.linkedin.com/jobs/view/{n}/'} for n in (1,2)]
        def fake(r):
            if '/1/' in r['sourceUrl']: raise OSError('unavailable')
            return {**r,'status':'fetched','text':'JD'}
        results,stats=card_jds.fetch_cards(rows,fetch=fake)
        self.assertEqual([r['status'] for r in results],['needs_browser','fetched'])
        self.assertEqual(stats['requested'],2)

    def test_explicit_restrictions_but_not_ambiguous_export_exceptions(self):
        self.assertEqual(card_jds.hard_exclusion('U.S. citizenship is required.'),'citizenship_required')
        self.assertIsNone(card_jds.hard_exclusion('Authorized without current or future sponsorship.'))
        self.assertIsNone(card_jds.hard_exclusion('We do not offer visa sponsorship. OPT candidates welcome.'))
        self.assertIsNone(card_jds.hard_exclusion('No U.S. citizenship required. We offer sponsorship.'))
        self.assertIsNone(card_jds.hard_exclusion('ITAR: citizen or eligible to obtain export authorization.'))

if __name__=='__main__':unittest.main()
