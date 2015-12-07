# encoding=utf8
from nose.tools import (
    eq_,
    set_trace,
)
import os
import datetime
from . import DatabaseTest
from ..config import (
    Configuration,
    temp_config,
)
from collections import Counter
from ..controller import (
    CirculationManager,
    CirculationManagerController,
)
from ..core.app_server import (
    load_lending_policy
)
from ..core.model import (
    Patron,
    DeliveryMechanism,
    Representation,
    Loan,
    Hold,
    DataSource,
    Identifier,
    Complaint,
    SessionManager,
    CachedFeed,
    get_one,
)
from ..core.lane import (
    Facets,
    Pagination,
)
import flask
from ..problem_details import *
from ..circulation_exceptions import *
from ..circulation import (
    HoldInfo,
    LoanInfo,
)

from ..lanes import make_lanes_default
from flask import url_for
from ..core.util.cdn import cdnify
import base64
import feedparser
from ..core.opds import (
    OPDSFeed,
    AcquisitionFeed,
)
from ..opds import CirculationManagerAnnotator
from lxml import etree
import random
import json

class TestCirculationManager(CirculationManager):

    def cdn_url_for(self, view, *args, **kwargs):
        base_url = url_for(view, *args, **kwargs)
        return cdnify(base_url, "http://cdn/")

class ControllerTest(DatabaseTest):
    def setup(self):
        super(ControllerTest, self).setup()

        os.environ['AUTOINITIALIZE'] = "False"
        from ..app import app
        del os.environ['AUTOINITIALIZE']
        self.app = app

        # Create two English books and a French book.
        self.english_1 = self._work(
            "Quite British", "John Bull", language="eng", fiction=True,
            with_open_access_download=True
        )

        self.english_2 = self._work(
            "Totally American", "Uncle Sam", language="eng", fiction=False,
            with_open_access_download=True
        )
        self.french_1 = self._work(
            u"Très Français", "Marianne", language="fre", fiction=False,
            with_open_access_download=True
        )

        self.valid_auth = 'Basic ' + base64.b64encode('200:2222')
        self.invalid_auth = 'Basic ' + base64.b64encode('200:2221')

        with temp_config() as config:
            languages = Configuration.language_policy()
            languages[Configuration.LARGE_COLLECTION_LANGUAGES] = 'eng'
            languages[Configuration.SMALL_COLLECTION_LANGUAGES] = 'spa,chi'
            lanes = make_lanes_default(self._db)
            self.manager = TestCirculationManager(self._db, lanes=lanes, testing=True)
            self.app.manager = self.manager
            self.controller = CirculationManagerController(self.manager)

class TestBaseController(ControllerTest):

    def test_authenticated_patron_invalid_credentials(self):
        value = self.controller.authenticated_patron("5", "1234")
        eq_(value, INVALID_CREDENTIALS)

    def test_authenticated_patron_expired_credentials(self):
        value = self.controller.authenticated_patron("0", "0000")
        eq_(value, EXPIRED_CREDENTIALS)

    def test_authenticated_patron_correct_credentials(self):
        value = self.controller.authenticated_patron("5", "5555")
        assert isinstance(value, Patron)

    def test_load_lane(self):
        eq_(self.manager, self.controller.load_lane(None, None))
        chinese = self.controller.load_lane('chi', None)
        eq_(None, chinese.name)
        eq_("Chinese", chinese.display_name)
        eq_(["chi"], chinese.languages)

        english_sf = self.controller.load_lane('eng', "Science Fiction")
        eq_("Science Fiction", english_sf.display_name)
        eq_(["eng"], english_sf.languages)

        # __ is converted to /
        english_thriller = self.controller.load_lane('eng', "Suspense__Thriller")
        eq_("Suspense/Thriller", english_thriller.name)

        # Unlike with Chinese, there is no lane that contains all English books.
        english = self.controller.load_lane('eng', None)
        eq_(english.uri, NO_SUCH_LANE.uri)

        no_such_language = self.controller.load_lane('o10', None)
        eq_(no_such_language.uri, NO_SUCH_LANE.uri)
        eq_("Unrecognized language key: o10", no_such_language.detail)

        no_such_lane = self.controller.load_lane('eng', 'No such lane')
        eq_("No such lane: No such lane", no_such_lane.detail)

    def test_load_facets_from_request(self):
        with self.app.test_request_context('/?order=%s' % Facets.ORDER_TITLE):
            facets = self.controller.load_facets_from_request()
            eq_(Facets.ORDER_TITLE, facets.order)

        with self.app.test_request_context('/?order=bad_facet'):
            problemdetail = self.controller.load_facets_from_request()
            eq_(INVALID_INPUT.uri, problemdetail.uri)

    def test_load_pagination_from_request(self):
        with self.app.test_request_context('/?size=50&after=10'):
            pagination = self.controller.load_pagination_from_request()
            eq_(50, pagination.size)
            eq_(10, pagination.offset)

        with self.app.test_request_context('/'):
            pagination = self.controller.load_pagination_from_request()
            eq_(Pagination.DEFAULT_SIZE, pagination.size)
            eq_(0, pagination.offset)

        with self.app.test_request_context('/?size=string'):
            pagination = self.controller.load_pagination_from_request()
            eq_(INVALID_INPUT.uri, pagination.uri)
            eq_("Invalid size: string", pagination.detail)

        with self.app.test_request_context('/?after=string'):
            pagination = self.controller.load_pagination_from_request()
            eq_(INVALID_INPUT.uri, pagination.uri)
            eq_("Invalid offset: string", pagination.detail)

        with self.app.test_request_context('/?size=5000'):
            pagination = self.controller.load_pagination_from_request()
            eq_(100, pagination.size)

    def test_load_licensepool(self):
        licensepool = self._licensepool(edition=None)
        loaded_licensepool = self.controller.load_licensepool(
            licensepool.data_source.name, licensepool.identifier.identifier
        )
        eq_(licensepool, loaded_licensepool)

        problem_detail = self.controller.load_licensepool("bad data source", licensepool.identifier.identifier)
        eq_(INVALID_INPUT.uri, problem_detail.uri)
        
        problem_detail = self.controller.load_licensepool(licensepool.data_source.name, "bad identifier")
        eq_(NO_LICENSES.uri, problem_detail.uri)

    def test_load_licensepooldelivery(self):
        licensepool = self._licensepool(edition=None, with_open_access_download=True)
        lpdm = licensepool.delivery_mechanisms[0]
        delivery = self.controller.load_licensepooldelivery(licensepool, lpdm.delivery_mechanism.id)
        eq_(lpdm, delivery)

        adobe_licensepool = self._licensepool(edition=None, with_open_access_download=False)
        problem_detail = self.controller.load_licensepooldelivery(adobe_licensepool, lpdm.delivery_mechanism.id)
        eq_(BAD_DELIVERY_MECHANISM.uri, problem_detail.uri)

    def test_apply_borrowing_policy_when_holds_prohibited(self):
        
        patron = self.controller.authenticated_patron("5", "5555")
        with temp_config() as config:
            config['policies'][Configuration.HOLD_POLICY] = Configuration.HOLD_POLICY_HIDE
            work = self._work(with_license_pool=True)
            [pool] = work.license_pools
            pool.licenses_available = 0
            problem = self.controller.apply_borrowing_policy(
                patron, pool
            )
            eq_(FORBIDDEN_BY_POLICY.uri, problem.uri)

    def test_apply_borrowing_policy_for_audience_restriction(self):

        patron = self.controller.authenticated_patron("5", "5555")
        work = self._work(with_license_pool=True)
        [pool] = work.license_pools

        self.manager.lending_policy = load_lending_policy(
            {
                "60": {"audiences": ["Children"]}, 
                "152": {"audiences": ["Children"]}, 
                "62": {"audiences": ["Children"]}
            }
        )

        patron._external_type = '10'
        eq_(None, self.controller.apply_borrowing_policy(patron, pool))

        patron._external_type = '152'
        problem = self.controller.apply_borrowing_policy(patron, pool)
        eq_(FORBIDDEN_BY_POLICY.uri, problem.uri)


class TestIndexController(ControllerTest):
    
    def test_simple_redirect(self):
        with temp_config() as config:
            config['policies'][Configuration.ROOT_LANE_POLICY] = None
            with self.app.test_request_context('/'):
                response = self.manager.index_controller()
                eq_(302, response.status_code)
                eq_("http://cdn/groups/", response.headers['location'])

    def test_authenticated_patron_root_lane(self):
        with temp_config() as config:
            config['policies'][Configuration.ROOT_LANE_POLICY] = { "2": ["eng", "Adult Fiction"] }
            config['policies'][Configuration.EXTERNAL_TYPE_REGULAR_EXPRESSION] = "^(.)"
            with self.app.test_request_context(
                "/", headers=dict(Authorization=self.invalid_auth)):
                response = self.manager.index_controller()
                eq_(401, response.status_code)

            with self.app.test_request_context(
                "/", headers=dict(Authorization=self.valid_auth)):
                response = self.manager.index_controller()
                eq_(302, response.status_code)
                eq_("http://cdn/groups/eng/Adult%20Fiction", response.headers['location'])

            config['policies'][Configuration.ROOT_LANE_POLICY] = { "2": None }
            with self.app.test_request_context(
                "/", headers=dict(Authorization=self.valid_auth)):
                response = self.manager.index_controller()
                eq_(302, response.status_code)
                eq_("http://cdn/groups/", response.headers['location'])


class TestLoanController(ControllerTest):
    def setup(self):
        super(TestLoanController, self).setup()
        self.pool = self.english_1.license_pools[0]
        self.mech2 = self.pool.set_delivery_mechanism(
            Representation.PDF_MEDIA_TYPE, DeliveryMechanism.NO_DRM,
            None
        )
        self.edition = self.pool.edition
        self.data_source = self.edition.data_source
        self.identifier = self.edition.primary_identifier

    def test_borrow_success(self):
        with self.app.test_request_context(
                "/", headers=dict(Authorization=self.valid_auth)):
            self.manager.loans.authenticated_patron_from_request()
            response = self.manager.loans.borrow(
                self.data_source.name, self.identifier.identifier)

            # A loan has been created for this license pool.
            loan = get_one(self._db, Loan, license_pool=self.pool)
            assert loan != None
            # The loan has yet to be fulfilled.
            eq_(None, loan.fulfillment)

            # We've been given an OPDS feed with one entry, which tells us how 
            # to fulfill the license.
            eq_(201, response.status_code)
            feed = feedparser.parse(response.get_data())
            [entry] = feed['entries']
            fulfillment_links = [x['href'] for x in entry['links']
                                if x['rel'] == OPDSFeed.ACQUISITION_REL]
            [mech1, mech2] = self.pool.delivery_mechanisms
            expects = [url_for('fulfill', data_source=self.data_source.name,
                              identifier=self.identifier.identifier, 
                              mechanism_id=mech.delivery_mechanism.id,
                               _external=True) for mech in [mech1, mech2]]
            eq_(set(expects), set(fulfillment_links))

            # Now let's try to fulfill the loan.
            response = self.manager.loans.fulfill(
                self.data_source.name, self.identifier.identifier,
                mech1.delivery_mechanism.id
            )
            eq_(302, response.status_code)
            eq_(mech1.resource.url,
                response.headers['Location'])

            # The mechanism we used has been registered with the loan.
            eq_(mech1, loan.fulfillment)

            # Now that we've set a mechanism, we can fulfill the loan
            # again without specifying a mechanism.
            response = self.manager.loans.fulfill(
                self.data_source.name, self.identifier.identifier
            )
            eq_(302, response.status_code)
            eq_(mech1.resource.url,
                response.headers['Location'])

            # But we can't use some other mechanism -- we're stuck with
            # the first one we chose.
            response = self.manager.loans.fulfill(
                self.data_source.name, self.identifier.identifier,
                mech2.delivery_mechanism.id
            )

            eq_(400, response.status_code)
            assert "You already fulfilled this loan as application/epub+zip (DRM-free), you can't also do it as application/pdf (DRM-free)" in response.detail

    def test_borrow_creates_hold_when_no_available_copies(self):
         threem_edition, pool = self._edition(
             with_open_access_download=False,
             data_source_name=DataSource.THREEM,
             identifier_type=Identifier.THREEM_ID,
             with_license_pool=True,
         )
         threem_book = self._work(
             primary_edition=threem_edition,
         )
         pool.licenses_available = 0
         pool.open_access = False

         with self.app.test_request_context(
                 "/", headers=dict(Authorization=self.valid_auth)):
             self.manager.loans.authenticated_patron_from_request()
             self.manager.circulation.queue_checkout(NoAvailableCopies())
             self.manager.circulation.queue_hold(HoldInfo(
                 pool.identifier.type,
                 pool.identifier.identifier,
                 datetime.datetime.utcnow(),
                 datetime.datetime.utcnow() + datetime.timedelta(seconds=3600),
                 1,
             ))
             response = self.manager.loans.borrow(
                 DataSource.THREEM, pool.identifier.identifier)
             eq_(201, response.status_code)

             # A hold has been created for this license pool.
             hold = get_one(self._db, Hold, license_pool=pool)
             assert hold != None

    def test_revoke_loan(self):
         with self.app.test_request_context(
                 "/", headers=dict(Authorization=self.valid_auth)):
             patron = self.manager.loans.authenticated_patron_from_request()
             loan, newly_created = self.pool.loan_to(patron)

             self.manager.circulation.queue_checkin(True)

             response = self.manager.loans.revoke(self.pool.data_source.name, self.pool.identifier.identifier)

             eq_(200, response.status_code)
             
    def test_revoke_hold(self):
         with self.app.test_request_context(
                 "/", headers=dict(Authorization=self.valid_auth)):
             patron = self.manager.loans.authenticated_patron_from_request()
             hold, newly_created = self.pool.on_hold_to(patron, position=0)

             self.manager.circulation.queue_release_hold(True)

             response = self.manager.loans.revoke(self.pool.data_source.name, self.pool.identifier.identifier)

             eq_(200, response.status_code)

    def test_3m_cant_revoke_hold_if_reserved(self):
         threem_edition, pool = self._edition(
             with_open_access_download=False,
             data_source_name=DataSource.THREEM,
             identifier_type=Identifier.THREEM_ID,
             with_license_pool=True,
         )
         threem_book = self._work(
             primary_edition=threem_edition,
         )
         pool.open_access = False

         with self.app.test_request_context(
                 "/", headers=dict(Authorization=self.valid_auth)):
             patron = self.manager.loans.authenticated_patron_from_request()
             hold, newly_created = pool.on_hold_to(patron, position=0)
             response = self.manager.loans.revoke(pool.data_source.name, pool.identifier.identifier)
             eq_(400, response.status_code)
             eq_(CANNOT_RELEASE_HOLD.uri, response.uri)
             eq_("Cannot release a hold once it enters reserved state.", response.detail)

    def test_active_loans(self):
        with self.app.test_request_context(
                "/", headers=dict(Authorization=self.valid_auth)):
            patron = self.manager.loans.authenticated_patron_from_request()
            response = self.manager.loans.sync()
            assert not "<entry>" in response.data
            assert response.headers['Cache-Control'].startswith('private,')

        overdrive_edition, overdrive_pool = self._edition(
            with_open_access_download=False,
            data_source_name=DataSource.OVERDRIVE,
            identifier_type=Identifier.OVERDRIVE_ID,
            with_license_pool=True,
        )
        overdrive_book = self._work(
            primary_edition=overdrive_edition,
        )
        overdrive_pool.open_access = False

        threem_edition, threem_pool = self._edition(
            with_open_access_download=False,
            data_source_name=DataSource.THREEM,
            identifier_type=Identifier.THREEM_ID,
            with_license_pool=True,
        )
        threem_book = self._work(
            primary_edition=threem_edition,
        )
        threem_pool.licenses_available = 0
        threem_pool.open_access = False
        
        loan = LoanInfo(
            overdrive_pool.identifier.type,
            overdrive_pool.identifier.identifier,
            datetime.datetime.utcnow(),
            datetime.datetime.utcnow() + datetime.timedelta(seconds=3600),
        )
        hold = HoldInfo(
            threem_pool.identifier.type,
            threem_pool.identifier.identifier,
            datetime.datetime.utcnow(),
            datetime.datetime.utcnow() + datetime.timedelta(seconds=3600),
            0,
        )
        self.manager.circulation.set_patron_activity([loan], [hold])

        with self.app.test_request_context(
                "/", headers=dict(Authorization=self.valid_auth)):
            patron = self.manager.loans.authenticated_patron_from_request()
            response = self.manager.loans.sync()

            feed = feedparser.parse(response.data)
            entries = feed['entries']

            overdrive_entry = [entry for entry in entries if entry['title'] == overdrive_book.title][0]
            threem_entry = [entry for entry in entries if entry['title'] == threem_book.title][0]

            eq_(overdrive_entry['opds_availability']['status'], 'available')
            eq_(threem_entry['opds_availability']['status'], 'ready')
            
            overdrive_links = overdrive_entry['links']
            fulfill_link = [x for x in overdrive_links if x['rel'] == 'http://opds-spec.org/acquisition'][0]['href']
            revoke_link = [x for x in overdrive_links if x['rel'] == OPDSFeed.REVOKE_LOAN_REL][0]['href']
            threem_links = threem_entry['links']
            borrow_link = [x for x in threem_links if x['rel'] == 'http://opds-spec.org/acquisition/borrow'][0]['href']
            threem_revoke_links = [x for x in threem_links if x['rel'] == OPDSFeed.REVOKE_LOAN_REL]

            assert "%s/%s/fulfill" % (overdrive_pool.data_source.name, overdrive_pool.identifier.identifier) in fulfill_link
            assert "%s/%s/revoke" % (overdrive_pool.data_source.name, overdrive_pool.identifier.identifier) in revoke_link
            assert "%s/%s/borrow" % (threem_pool.data_source.name, threem_pool.identifier.identifier) in borrow_link
            eq_(0, len(threem_revoke_links))


class TestWorkController(ControllerTest):

    def test_permalink(self):
        [lp] = self.english_1.license_pools
        with self.app.test_request_context("/"):
            response = self.manager.work_controller.permalink(lp.data_source.name, lp.identifier.identifier)
            annotator = CirculationManagerAnnotator(None, None)
            expect = etree.tostring(
                AcquisitionFeed.single_entry(
                    self._db, self.english_1, annotator
                )
            )
        eq_(200, response.status_code)
        eq_(expect, response.data)
        eq_(OPDSFeed.ENTRY_TYPE, response.headers['Content-Type'])

    def test_report_problem_get(self):
        [lp] = self.english_1.license_pools
        with self.app.test_request_context("/"):
            response = self.manager.work_controller.report(lp.data_source.name, lp.identifier.identifier)
        eq_(200, response.status_code)
        eq_("text/uri-list", response.headers['Content-Type'])
        for i in Complaint.VALID_TYPES:
            assert i in response.data

    def test_report_problem_post_success(self):
        error_type = random.choice(list(Complaint.VALID_TYPES))
        data = json.dumps({ "type": error_type,
                            "source": "foo",
                            "detail": "bar"}
        )
        [lp] = self.english_1.license_pools
        with self.app.test_request_context("/", method="POST", data=data):
            response = self.manager.work_controller.report(lp.data_source.name, lp.identifier.identifier)
        eq_(201, response.status_code)
        [complaint] = lp.complaints
        eq_(error_type, complaint.type)
        eq_("foo", complaint.source)
        eq_("bar", complaint.detail)


class TestFeedController(ControllerTest):

    def test_feed(self):
        SessionManager.refresh_materialized_views(self._db)
        with self.app.test_request_context("/"):
            response = self.manager.opds_feeds.feed('eng', 'Adult Fiction')

            assert self.english_1.title in response.data
            assert self.english_2.title not in response.data
            assert self.french_1.title not in response.data

    def test_multipage_feed(self):
        self._work("fiction work", language="eng", fiction=True, with_open_access_download=True)
        SessionManager.refresh_materialized_views(self._db)
        with self.app.test_request_context("/?size=1"):
            response = self.manager.opds_feeds.feed('eng', 'Adult Fiction')

            feed = feedparser.parse(response.data)
            entries = feed['entries']
            
            eq_(1, len(entries))

            links = feed['feed']['links']
            next_link = [x for x in links if x['rel'] == 'next'][0]['href']
            assert 'after=1' in next_link
            assert 'size=1' in next_link

            facet_links = [x for x in links if x['rel'] == 'http://opds-spec.org/facet']
            assert any('order=title' in x['href'] for x in facet_links)
            assert any('order=author' in x['href'] for x in facet_links)

            search_link = [x for x in links if x['rel'] == 'search'][0]['href']
            assert search_link.endswith('/search/eng/Adult%20Fiction')

            shelf_link = [x for x in links if x['rel'] == 'http://opds-spec.org/shelf'][0]['href']
            assert shelf_link.endswith('/loans/')


    def test_groups(self):
        with temp_config() as config:
            config['policies'][Configuration.GROUPS_MAX_AGE_POLICY] = 10
            config['policies'][Configuration.MINIMUM_FEATURED_QUALITY] = 0
            config['policies'][Configuration.FEATURED_LANE_SIZE] = 2

            for i in range(2):
                self._work("fiction work %i" % i, language="eng", fiction=True, with_open_access_download=True)
                self._work("nonfiction work %i" % i, language="eng", fiction=False, with_open_access_download=True)
        
            SessionManager.refresh_materialized_views(self._db)
            with self.app.test_request_context("/"):
                response = self.manager.opds_feeds.groups(None, None)

                feed = feedparser.parse(response.data)
                entries = feed['entries']
                
                counter = Counter()
                for entry in entries:
                    links = [x for x in entry.links if x['rel'] == 'collection']
                    for link in links:
                        counter[link['title']] += 1
                eq_(2, counter['Nonfiction'])
                eq_(2, counter['Fiction'])
                eq_(1, counter['Other Languages'])
        
