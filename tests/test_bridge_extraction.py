import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
class ExtensionExtractionTests(unittest.TestCase):
    def test_apply_redirect_link_is_unwrapped_but_other_redirects_ignored(self):
        html='<a href="https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fjobs.lever.co%2Facme%2F123">Apply</a><a href="https://evil.com/?url=https%3A%2F%2Fjobs.lever.co%2Fwrong%2F456">Apply</a>'
        self.assertEqual(self.read(html)['applicationLinks'],['https://jobs.lever.co/acme/123'])
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(channel='msedge',headless=True)
        cls.script=(Path(__file__).resolve().parents[1]/'browser-extension/jobsearch-bridge/extract.js').read_text(encoding='utf-8-sig')
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def read(self,html,url='https://www.linkedin.com/jobs/view/123/'):
        page=self.browser.new_page();page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html; charset=utf-8'))
        page.goto(url);page.add_script_tag(content=self.script)
        result=page.evaluate('readJobPage()');page.close();return result
    def test_complete_jd_not_whole_page(self):
        jd='Build Python services and agent workflows. '*10
        result=self.read('<h1>Engineer</h1><div class="job-details-jobs-unified-top-card__company-name">Acme</div><div class="jobs-description__content">'+jd+'</div><footer>private unrelated account content</footer>')
        self.assertEqual(result['company'],'Acme');self.assertNotIn('private',result['text'])
    def test_login_and_verification_stop(self):
        self.assertEqual(self.read('<input type=password>')['blocked'],'login_required')
        self.assertEqual(self.read('<p>Verify you are human</p>')['blocked'],'security_verification')
    def test_card_links_confined_to_source(self):
        result=self.read('<a href="https://evil.com/jobs/view/123">Bad</a><a href="/jobs/view/engineer-456/?tracking=x">Good</a><a href="/jobs/view/456/">Duplicate</a>', 'https://www.linkedin.com/jobs/search/')
        self.assertEqual(result['cards'],[{'url':'https://www.linkedin.com/jobs/view/456/','title':'Good'}])

    def test_new_logged_in_layout(self):
        jd='Build backend services with Python. '*20
        html='<title>Backend Engineer | Acme | LinkedIn</title><div><div><div aria-label="Company, Acme."></div></div><p>Austin, TX · Reposted 1 hour ago · 5 applicants</p></div><section><div><h2>About the job</h2></div><div>'+jd+'</div></section><section><h2>About the company</h2><p>Unrelated footer</p></section>'
        result=self.read(html)
        self.assertEqual(result['title'],'Backend Engineer')
        self.assertEqual(result['company'],'Acme')
        self.assertEqual(result['location'],'Austin, TX')
        self.assertNotIn('Unrelated footer',result['text'])
        self.assertGreater(result['diagnostics']['jdLength'],200)

    def test_handshake_details_and_opt_facts(self):
        jd='Build software for automotive systems. '*20
        html='<h1>Jobs</h1><div><a href="/e/1" aria-label="Acme">Acme</a><a href="/jobs/123"><h1>Software Engineer I</h1></a></div><section><div><h3>Job description</h3></div><div>'+jd+'</div></section><section><div><h3>At a glance</h3></div><p>Onsite, based in Austin, TX</p><p>Eligible for visa sponsorship and open to candidates with OPT/CPT</p></section>'
        result=self.read(html,'https://smu.joinhandshake.com/job-search/123')
        self.assertEqual(result['title'],'Software Engineer I')
        self.assertEqual(result['company'],'Acme')
        self.assertEqual(result['location'],'Austin, TX')
        self.assertIn('OPT/CPT',result['text'])

    def test_collapsed_handshake_is_reported(self):
        html='<section><div><h3>Job description</h3></div><p>'+('Preview text. '*20)+'</p><button type="button" aria-label="Show more (job description)">More</button></section>'
        result=self.read(html,'https://smu.joinhandshake.com/job-search/123')
        self.assertTrue(result['collapsed'])

    def test_search_empty_and_loading_distinguished(self):
        self.assertTrue(self.read('<p>No matching jobs found</p>','https://www.linkedin.com/jobs/search/')['emptyResults'])
        self.assertTrue(self.read('<div role="progressbar"></div>','https://www.linkedin.com/jobs/search/')['loading'])

    def test_handshake_search_cards_exclude_similar_jobs(self):
        html='<input placeholder="Describe a job you want" value="backend"><div data-hook="job-result-card | 123"><a role="button" aria-label="Acme Backend Engineer" href="/job-search/123?query=backend"></a></div><a href="/jobs/999">Unrelated similar job</a>'
        r=self.read(html,'https://smu.joinhandshake.com/job-search?query=backend')
        self.assertEqual(r['searchKeyword'],'backend')
        self.assertEqual(r['cards'],[{'url':'https://smu.joinhandshake.com/job-search/123','title':'Acme Backend Engineer'}])

    def test_linkedin_button_cards_are_read_and_deduplicated(self):
        html='<div role="button" componentkey="job-card-component-ref-456"><button aria-label="Dismiss Backend Engineer job"></button></div><div role="button" componentkey="job-card-component-ref-789"><button aria-label="Dismiss Data Engineer job"></button></div><a href="/jobs/view/456/">Backend Engineer</a><div role="button" componentkey="job-card-component-ref-999">Unrelated</div>'
        r=self.read(html,'https://www.linkedin.com/jobs/search-results?keywords=backend')
        self.assertEqual(len(r['cards']),2)
        self.assertEqual(r['cards'][1],{'url':'https://www.linkedin.com/jobs/view/789/','title':'Data Engineer'})

    def test_application_links_are_read_without_navigation(self):
        r=self.read('<a href="https://jobs.lever.co/acme/123/apply">Apply</a><a href="https://jobs.lever.co/acme/123/apply">Duplicate</a><a href="https://evil.example/apply">Wrong</a>')
        self.assertEqual(r['applicationLinks'],['https://jobs.lever.co/acme/123/apply'])
