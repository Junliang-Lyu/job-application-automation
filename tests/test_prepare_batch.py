import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_batch as batch
from urllib.parse import urlsplit, parse_qs

class BatchTests(unittest.TestCase):
    def test_search_link_encodes_keyword_location_and_freshness(self):
        url=batch.linkedin_search_url('backend C++','Dallas, TX',14,['1','2'],True)
        q=parse_qs(urlsplit(url).query)
        self.assertEqual(q['keywords'],['backend C++'])
        self.assertEqual(q['location'],['Dallas, TX'])
        self.assertEqual(q['f_TPR'],['r1209600'])
        self.assertEqual(q['f_AL'],['true'])

    def test_configured_search_controls_reach_browser_fetch_url(self):
        import fetch_jobs_browser as fetch
        from unittest.mock import patch
        with patch.object(fetch, '_scrape_linkedin_common', return_value=[]) as scrape:
            fetch.scrape_linkedin_all(None,'backend C++',5,set(),set(),location='Plano, TX',posted_days=7)
        q=parse_qs(urlsplit(scrape.call_args.args[1]).query)
        self.assertEqual(q['location'],['Plano, TX'])
        self.assertEqual(q['f_TPR'],['r604800'])
    def config(self):
        return dict(include_titles=['software','backend','ai engineer'], exclude_titles=['senior','staff'],
                    locations=['United States','Dallas','San Francisco'], preferred_locations=['Dallas'],
                    allow_unknown_location=False,max_per_company=1,max_per_source=5,max_candidates=5)

    def row(self, company, num, title='Software Engineer', location='Dallas, TX'):
        return dict(sourceUrl=f'https://job-boards.greenhouse.io/{company}/jobs/{num}',company=company,
                    title=title,location=location,text='Build Python APIs for customers.',status='fetched',_source='Greenhouse')

    def test_filter_before_diverse_selection(self):
        rows=[self.row('a',1),self.row('a',2),self.row('b',3,'Senior Software Engineer'),
              self.row('c',4,location='London, UK'),self.row('d',5),self.row('e',6,location='')]
        selected,rejected=batch.select_candidates(rows,self.config(),{'https://boards.greenhouse.io/a/jobs/1'})
        self.assertEqual([r['company'] for r in selected],['a','d'])
        self.assertEqual(len(rejected),4)

    def test_cache_reuses_url_alias_but_not_changed_content_or_prompt(self):
        a=self.row('a',1);b={**a,'sourceUrl':a['sourceUrl']+'?gh_src=li'}
        self.assertEqual(batch.cache_key(a,'prompt','model'),batch.cache_key(b,'prompt','model'))
        self.assertNotEqual(batch.cache_key(a,'prompt','model'),batch.cache_key({**a,'text':'changed'},'prompt','model'))
        self.assertNotEqual(batch.cache_key(a,'prompt','model'),batch.cache_key(a,'new prompt','model'))

    def test_material_failure_is_not_ready_and_does_not_stop_next_job(self):
        rows=[self.row('a',1),self.row('b',2)]
        def analyze(r): return {'fit_score':85,'worth_applying':True,'cover_letter_body':'Factual body.'}
        def pdf(r,a):
            if r['company']=='a': raise RuntimeError('PDF failed')
            return 'verified.pdf'
        results=batch.prepare_candidates(rows,analyze,pdf,min_score=70)
        self.assertEqual([r['status'] for r in results],['materials_failed','ready_for_review'])
        self.assertIn('PDF failed',results[0]['error'])

    def test_preserves_in_progress_and_terminal_state(self):
        old=[{'job_key':'greenhouse:1','status':'submission_unknown'}, {'job_key':'greenhouse:2','status':'applied'}]
        new=[{'job_key':'greenhouse:1','status':'ready_for_review'}, {'job_key':'greenhouse:2','status':'ready_for_review'}]
        self.assertEqual(batch.merge_progress(old,new),old)

    def test_reportlab_output_is_validated(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.pdf';p.write_text('not a PDF')
            with self.assertRaises(ValueError): batch.validate_pdf(p)

    def test_attempt_result_cannot_claim_submission_without_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);batch.save(run/'queue.json',[{'job_key':'x','status':'ready_for_review'}])
            with self.assertRaises(ValueError): batch.record_result(run,'x','applied','done')
            batch.record_result(run,'x','awaiting_authorization','Reviewer rejected broad permission')
            row=json.loads((run/'queue.json').read_text())[0]
            self.assertEqual(row['status'],'awaiting_authorization')
            self.assertTrue((run/'events.jsonl').exists())

    def test_replays_journal_if_checkpoint_write_was_interrupted(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);batch.save(run/'queue.json',[{'job_key':'x','status':'ready_for_review'}])
            with patch.object(batch,'save',side_effect=OSError('interrupted')):
                with self.assertRaises(OSError): batch.record_result(run,'x','submission_unknown','click result unknown')
            self.assertEqual(batch.load_progress(run)[0]['status'],'submission_unknown')

    def test_discovery_failure_is_not_reported_as_no_matching_jobs(self):
        state=batch.discovery_outcome([{'status':'needs_human','error':'network unavailable'}])
        self.assertTrue(state['discovery_failed'])
        self.assertEqual(state['fetch_failures'],1)
        self.assertFalse(batch.discovery_outcome([])['discovery_failed'])

if __name__=='__main__': unittest.main()

class NationwideLocationTests(unittest.TestCase):
    def test_us_scope_does_not_exclude_san_diego(self):
        config={'locations':['Dallas'],'location_scope':'us'}
        self.assertTrue(batch.location_allowed('San Diego, CA',config))
        self.assertTrue(batch.location_allowed('Anchorage, AK',config))
        self.assertFalse(batch.location_allowed('London, UK',config))
        self.assertFalse(batch.location_allowed('Toronto, ON',config))

class UnknownRequirementsTests(unittest.TestCase):
    def test_unknown_does_not_equal_conflict(self):
        row={'sourceUrl':'https://www.linkedin.com/jobs/view/123','title':'Engineer','company':'Acme','text':'Build services'}
        analysis={'fit_score':75,'worth_applying':False,'unknown_requirements':['Mandarin proficiency'],'confirmed_conflicts':[],'cover_letter_body':'Supported project experience.'}
        result=batch.prepare_candidates([row],lambda r:analysis,lambda r,a:'letter.pdf')[0]
        # 2026-10-27: a non-hard unknown is a note, not a review hold.
        self.assertEqual(result['status'],'ready_for_review')
        self.assertEqual(result['hints'],['Mandarin proficiency'])
        analysis['unknown_requirements']=['US Citizens only (JD hard requirement)']
        result=batch.prepare_candidates([row],lambda r:analysis,lambda r,a:'letter.pdf')[0]
        self.assertEqual(result['status'],'needs_qualification_review')
        analysis['confirmed_conflicts']=['US citizenship required']
        result=batch.prepare_candidates([row],lambda r:analysis,lambda r,a:'letter.pdf')[0]
        self.assertEqual(result['status'],'not_recommended')
