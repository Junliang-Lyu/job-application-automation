"""Local-only browser acceptance fixtures. No employers or personal data."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assisted_fill import fill_page


class BrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(channel='msedge',headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.page=self.browser.new_page()
        self.addCleanup(self.page.close)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.resume=Path(self.tmp.name)/'test_resume.pdf';self.resume.write_bytes(b'%PDF-1.4\n%%EOF')
        self.events=[]
        self.facts={'Name':'Test Applicant','Primary Application Email':'test@example.com',
                    'University':'Test University','Future Employer Sponsorship Required':'Yes'}

    def fixture(self,fields):
        self.page.set_content('<form onsubmit="event.preventDefault();document.body.dataset.submitted=\'yes\'">'+fields+'<button type="submit">Submit</button></form>')

    def fill(self):
        fill_page(self.page,self.facts,self.resume,lambda status,message:self.events.append((status,message)))
        self.assertIsNone(self.page.locator('body').get_attribute('data-submitted'))

    def test_basic_fields_resume_and_sponsorship_no_submission(self):
        self.fixture('<label>First name<input id="first"></label><label>Email<input id="email" type="email"></label>'
                     '<label>School<select id="school"><option value="">Choose</option><option>Test University</option></select></label>'
                     '<label>Resume<input id="resume" type="file"></label>'
                     '<fieldset><legend>Will you now or in the future require sponsorship?</legend><label>Yes<input type="radio" name="sponsor" value="yes"></label><label>No<input type="radio" name="sponsor" value="no"></label></fieldset>')
        self.fill()
        self.assertEqual(self.page.locator('#first').input_value(),'Test')
        self.assertEqual(self.page.locator('#school').input_value(),'Test University')
        self.assertEqual(self.page.locator('#resume').evaluate('(e)=>e.files[0].name'),'test_resume.pdf')
        self.assertTrue(self.page.locator('[value=yes]').is_checked())
        self.assertEqual(self.events[-1][0],'review')

    def test_conditional_open_question_stops_before_later_fields(self):
        self.fixture('<label>First name<input id="first" oninput="document.getElementById(\'why\').hidden=false"></label>'
                     '<label id="why" hidden>Why this company?<textarea></textarea></label><label>Email<input id="email"></label>')
        self.fill()
        self.assertEqual(self.events[-1][0],'paused')
        self.assertIn('Why this company?',self.events[-1][1])
        self.assertEqual(self.page.locator('#email').input_value(),'')

    def test_captcha_stops_before_any_fill(self):
        self.fixture('<p>Please verify you are human</p><label>First name<input id="first"></label>')
        self.fill()
        self.assertEqual(self.events[-1][0],'paused')
        self.assertEqual(self.page.locator('#first').input_value(),'')

    def test_preserves_existing_different_value(self):
        self.fixture('<label>First name<input value="Other" id="first"></label>')
        self.fill()
        self.assertEqual(self.page.locator('#first').input_value(),'Other')
        self.assertEqual(self.events[-1][0],'paused')


if __name__=='__main__':unittest.main()
