import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assisted_fill import answer_for, supported_url

class MappingTests(unittest.TestCase):
    def setUp(self):
        self.facts={'Name':'Test Applicant','Primary Application Email':'test@example.com (Gmail)',
                    'Future Employer Sponsorship Required':'Yes','Exact Graduation Date':'December 10, 2026',
                    'Current/Post-Graduation Work Authorization':'Eligible after graduation'}

    def test_known_contact(self):
        self.assertEqual(answer_for('First Name *',self.facts),'Test')
        self.assertEqual(answer_for('Email',self.facts),'test@example.com')

    def test_authorization_is_not_inferred(self):
        self.assertIsNone(answer_for('Are you legally authorized to work in the United States?',self.facts))
        self.assertEqual(answer_for('Will you now or in the future require sponsorship?',self.facts),'Yes')
        self.assertIsNone(answer_for('Are you authorized without current or future sponsorship?',self.facts))

    def test_unknowns_and_negative_questions(self):
        for label in ('Why join us?', 'Are you NOT willing to relocate?', 'Password','Gender','Years of SWE experience'):
            self.assertIsNone(answer_for(label,self.facts))

    def test_destinations(self):
        self.assertTrue(supported_url('https://jobs.lever.co/acme/123/apply'))
        self.assertTrue(supported_url('https://job-boards.greenhouse.io/acme/jobs/123'))
        for url in ('https://jobs.lever.co.evil.test/a','http://jobs.lever.co/a','https://linkedin.com/jobs/view/123','https://user:pw@jobs.lever.co/a'):
            self.assertFalse(supported_url(url))

if __name__=='__main__': unittest.main()
