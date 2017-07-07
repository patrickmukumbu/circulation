import json
import re
from nose.tools import set_trace
import contextlib
from copy import deepcopy
from flask.ext.babel import lazy_gettext as _
from core.config import (
    Configuration as CoreConfiguration,
    CannotLoadConfiguration,
    empty_config as core_empty_config,
    temp_config as core_temp_config,
)
from core.util import MoneyUtility
from core.lane import Facets
from core.model import ConfigurationSetting

class Configuration(CoreConfiguration):

    LENDING_POLICY = "lending"

    DEFAULT_OPDS_FORMAT = "simple_opds_entry"

    ROOT_LANE_POLICY = "root_lane"

    # The name of the sitewide url that points to the patron web catalog.
    PATRON_WEB_CLIENT_URL = u"Patron Web Client"

    # The name of the sitewide secret used to sign cookies for admin login.
    SECRET_KEY = u"secret_key"

    # The name of the per-library setting that sets the maximum amount
    # of fines a patron can have before losing lending privileges.
    MAX_OUTSTANDING_FINES = u"max_outstanding_fines"

    # The name of the per-library setting that sets the default email
    # address to use when notifying patrons of changes.
    DEFAULT_NOTIFICATION_EMAIL_ADDRESS = u"default_notification_email_address"

    # Name of the site-wide ConfigurationSetting containing the secret
    # used to sign bearer tokens.
    BEARER_TOKEN_SIGNING_SECRET = "bearer_token_signing_secret"

    # Names of per-library ConfigurationSettings that control
    # how detailed the lane configuration gets for various languages.
    LARGE_COLLECTION_LANGUAGES = "large_collections"
    SMALL_COLLECTION_LANGUAGES = "small_collections"
    TINY_COLLECTION_LANGUAGES = "tiny_collections"
    
    # Names of the library-wide link settings.
    TERMS_OF_SERVICE = 'terms-of-service'
    PRIVACY_POLICY = 'privacy-policy'
    COPYRIGHT = 'copyright'
    ABOUT = 'about'
    LICENSE = 'license'

    # A library with this many titles in a given language will be given
    # a large, detailed lane configuration for that language.
    LARGE_COLLECTION_CUTOFF = 10000

    # A library with this many titles in a given language will be
    # given separate fiction and nonfiction lanes for that language.
    SMALL_COLLECTION_CUTOFF = 500

    # A library with fewer titles than that will be given a single
    # lane containing all books in that language.
    
    SITEWIDE_SETTINGS = CoreConfiguration.SITEWIDE_SETTINGS + [
        {
            "key": BEARER_TOKEN_SIGNING_SECRET,
            "label": _("Internal signing secret for OAuth bearer tokens"),
        },
        {
            "key": SECRET_KEY,
            "label": _("Internal secret key for admin interface cookies"),
        },
        {
            "key": PATRON_WEB_CLIENT_URL,
            "label": _("URL of the web catalog for patrons"),
        },
    ]

    LIBRARY_SETTINGS = CoreConfiguration.LIBRARY_SETTINGS + [
        {
            "key": MAX_OUTSTANDING_FINES,
            "label": _("Maximum amount of fines a patron can have before losing lending privileges"),
        },
        {
            "key": DEFAULT_NOTIFICATION_EMAIL_ADDRESS,
            "label": _("Default email address to use when notifying patrons of changes"),
        },
        {
            "key": TERMS_OF_SERVICE,
            "label": _("Terms of Service URL"),
        },
        {
            "key": PRIVACY_POLICY,
            "label": _("Privacy Policy URL"),
        },
        {
            "key": COPYRIGHT,
            "label": _("Copyright URL"),
        },
        {
            "key": ABOUT,
            "label": _("About URL"),
        },
        {
            "key": LICENSE,
            "label": _("License URL"),
        },
    ]
    
    @classmethod
    def lending_policy(cls):
        return cls.policy(cls.LENDING_POLICY)

    @classmethod
    def root_lane_policy(cls):
        return cls.policy(cls.ROOT_LANE_POLICY)

    @classmethod
    def _collection_languages(cls, library, key):
        """Look up a list of languages in a library configuration.

        If the value is not set, estimate a value (and all related
        values) by looking at the library's collection.
        """
        setting = ConfigurationSetting.for_library(
            cls.LARGE_COLLECTION_LANGUAGES, library
        )
        if setting.value is None:
            cls.estimate_language_collections_for_library()
        return setting.json_value
    
    @classmethod
    def large_collection_languages(cls, library):
        return cls._collection_languages(
            library, cls.LARGE_COLLECTION_LANGUAGES
        )

    @classmethod
    def small_collection_languages(cls, library):
        return cls._collection_languages(
            library, cls.SMALL_COLLECTION_LANGUAGES
        )

    @classmethod
    def tiny_collection_languages(cls, library):
        return cls._collection_languages(
            library, cls.TINY_COLLECTION_LANGUAGES
        )

    @classmethod
    def max_outstanding_fines(cls, library):
        max_fines = ConfigurationSetting.for_library(
            cls.MAX_OUTSTANDING_FINES, library
        ).value
        return MoneyUtility.parse(max_fines)
    
    @classmethod
    def load(cls, _db=None):
        CoreConfiguration.load(_db)
        cls.instance = CoreConfiguration.instance

    @classmethod
    def estimate_language_collections_for_library(cls, library):
        """Guess at appropriate values for the given library for
        LARGE_COLLECTION_LANGUAGES, SMALL_COLLECTION_LANGUAGES, and
        TINY_COLLECTION_LANGUAGES. Set configuration values
        appropriately, overriding any previous values.
        """
        holdings = library.estimated_holdings_by_language()
        large, small, tiny = cls.classify_holdings(holdings)
        for setting, value in (
                (cls.LARGE_COLLECTION_LANGUAGES, large),
                (cls.SMALL_COLLECTION_LANGUAGES, small),
                (cls.TINY_COLLECTION_LANGUAGES, tiny),
        ):
            ConfigurationSetting.for_library(
                setting, library).value = json.dumps(value)

    @classmethod
    def classify_holdings(cls, works_by_language):
        """Divide languages into 'large', 'small', and 'tiny' colletions based
        on the number of works available for each.

        :param works_by_language: A Counter mapping languages to the
        number of active works available for that language.  The
        output of `Library.estimated_holdings_by_language` is a good
        thing to pass in.

        :return: a 3-tuple of lists (large, small, tiny).
        """
        large = []
        small = []
        tiny = []
        result = [large, small, tiny]
        # The single most common language always gets a large
        # collection.
        #
        # Otherwise, it depends on how many works are in the
        # collection.
        for language, num_works in works_by_language.most_common():
            if not large:
                bucket = large
            elif num_works > cls.LARGE_COLLECTION_CUTOFF:
                bucket = large
            elif num_works > cls.SMALL_COLLECTION_CUTOFF:
                bucket = small
            else:
                bucket = tiny
            bucket.append(language)

        if not large:
            # In the absence of any information, assume we have an
            # English collection and nothing else.
            large.append('eng')
            
        return result        
        
        
@contextlib.contextmanager
def empty_config():
    with core_empty_config({}, [CoreConfiguration, Configuration]) as i:
        yield i

@contextlib.contextmanager
def temp_config(new_config=None, replacement_classes=None):
    all_replacement_classes = [CoreConfiguration, Configuration]
    if replacement_classes:
        all_replacement_classes.extend(replacement_classes)
    with core_temp_config(new_config, all_replacement_classes) as i:
        yield i
