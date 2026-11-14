"""Exercise the real detector in Chromium against controlled HTML, never fake a live sale."""
from pathlib import Path
import unittest
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright, 'Playwright/browser required; runs in GitHub Actions')
class TicketDetector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls.script = Path(__file__).with_name('detect.js').read_text()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def detect(self, controls, heading='1-DAY <span>LOWER</span>', extra='', hidden=False):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content('''<button role="tab" aria-controls="wave2">WAVE 2</button>
          <section id="wave1"><div class="ticket-card"><h3>1-DAY LOWER</h3>
          <a href="https://tickets.example/wrong">BUY NOW</a></div></section>
          <section id="wave2" %s><div class="ticket-card"><h3>%s</h3>%s</div>%s</section>'''
          % ('hidden' if hidden else '', heading, controls, extra))
        return page.evaluate(self.script)

    def test_coming_soon_only_in_correct_card(self):
        self.assertEqual(self.detect('<button>COMING SOON</button>')['status'], 'coming_soon')

    def test_disabled_coming_soon_is_a_valid_status(self):
        self.assertEqual(self.detect('<button disabled>COMING SOON</button>')['status'], 'coming_soon')

    def test_sold_out(self):
        self.assertEqual(self.detect('<button>Sold out</button>')['status'], 'sold_out')

    def test_sale_with_multiline_label(self):
        r = self.detect('<a href="https://tickets.example/lower">Buy\n Now</a>')
        self.assertEqual(r['status'], 'sale_link')
        self.assertEqual(r['url'], 'https://tickets.example/lower')

    def test_inactive_wave2_panel_can_still_be_checked(self):
        self.assertEqual(self.detect('<button>COMING SOON</button>', hidden=True)['status'], 'coming_soon')

    def test_other_category_does_not_override_lower(self):
        extra = '<div class="ticket-card"><h3>1-DAY UPPER</h3><a href="https://tickets.example/upper">Buy Now</a></div>'
        self.assertEqual(self.detect('<button>Coming soon</button>', extra=extra)['status'], 'coming_soon')

    def test_duplicate_target_is_rejected(self):
        with self.assertRaises(Exception):
            self.detect('<button>Coming soon</button>', extra='<div class="ticket-card"><h3>1-DAY LOWER</h3></div>')

    def test_changed_heading_fails_closed(self):
        with self.assertRaises(Exception):self.detect('<button>Sold out</button>', heading='3-DAY LOWER')

    def test_conflicting_states_rejected(self):
        for controls in ['<button>Coming soon</button><button>Sold out</button>',
                         '<button>Coming soon</button><a href="https://tickets.example">Buy Now</a>']:
            with self.subTest(controls=controls), self.assertRaises(Exception):self.detect(controls)

    def test_disabled_hidden_and_placeholder_buy_are_not_sale(self):
        for attrs in ['href="#"', 'href=""', 'href="javascript:void(0)"',
                      'href="http://tickets.example"', 'href="https://tickets.example" aria-disabled="true"',
                      'href="https://tickets.example" disabled', 'href="https://tickets.example" hidden',
                      'href="https://tickets.example" class="disabled"',
                      'href="https://tickets.example" style="display:none"']:
            with self.subTest(attrs=attrs), self.assertRaises(Exception):self.detect('<a '+attrs+'>BUY NOW</a>')

    def test_hidden_old_button_does_not_hide_actual_sale(self):
        self.assertEqual(self.detect('<button hidden>Coming soon</button><a href="https://tickets.example">Buy Now</a>')['status'], 'sale_link')

    def test_two_purchase_links_require_review(self):
        with self.assertRaises(Exception):
            self.detect('<a href="https://tickets.example/a">Buy Now</a><a href="https://tickets.example/b">Buy Now</a>')
