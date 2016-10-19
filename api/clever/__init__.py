from nose.tools import set_trace
import logging
import base64
import json
import os
import datetime
from flask import url_for
from flask.ext.babel import lazy_gettext as _

from core.util.problem_detail import ProblemDetail

from api.authenticator import (
    OAuthAuthenticationProvider,
    PatronData,
)
from api.config import Configuration
from core.model import (
    get_one,
    get_one_or_create,
    Credential,
    DataSource,
    Patron,
)
from core.util.http import HTTP
from api.problem_details import *


UNSUPPORTED_CLEVER_USER_TYPE = pd(
    "http://librarysimplified.org/terms/problem/unsupported-clever-user-type",
    401,
    _("Your Clever user type is not supported."),
    _("Your Clever user type is not supported. You can request a code from First Book instead"),
)

CLEVER_NOT_ELIGIBLE = pd(
    "http://librarysimplified.org/terms/problem/clever-not-eligible",
    401,
    _("Your Clever account is not eligible to access this application."),
    _("Your Clever account is not eligible to access this application."),
)


# Load Title I NCES ID data from json.
TITLE_I_NCES_IDS = None
clever_dir = os.path.split(__file__)[0]

with open('%s/title_i.json' % clever_dir) as f:
    json_data = f.read()
    TITLE_I_NCES_IDS = json.loads(json_data)


class CleverAuthenticationAPI(OAuthAuthenticationProvider):

    URI = "http://librarysimplified.org/terms/auth/clever"

    # TODO: I think this can be removed because
    # "http://librarysimplified.org/authtype/OAuth-with-intermediary"
    # should be good enough.
    # METHOD = "http://librarysimplified.org/authtype/Clever"
    NAME = 'Clever'
    TOKEN_TYPE = "Clever token"
    TOKEN_DATA_SOURCE_NAME = 'Clever'

    CLEVER_OAUTH_URL = "https://clever.com/oauth/authorize?response_type=code&client_id=%s&redirect_uri=%s&state=%s"
    CLEVER_TOKEN_URL = "https://clever.com/oauth/tokens"
    CLEVER_API_BASE_URL = "https://api.clever.com"

    # To check Title I status we need state, which is associated with
    # a school in Clever's API. Any users at the district-level will
    # need to get a code from First Book instead.
    SUPPORTED_USER_TYPES = ['student', 'teacher']

    # Begin implementations of OAuthAuthenticationProvider abstract
    # methods.
    
    def external_authenticate_url(self, state):
        """Generate the URL provided by the OAuth provider which will present
        the patron with a login form.

        :param state: A state variable to be propagated through to the OAuth
        callback.
        """
        return self.CLEVER_OAUTH_URL % (
            self.client_id, self._server_redirect_uri(), state
        )

    def oauth_callback(self, _db, params):
        """Verify the incoming parameters with the OAuth provider. Create
        or look up appropriate database records.

        :return: A ProblemDetail if there's a problem. Otherwise, a
        3-tuple (Credential, Patron, PatronData). The Credential
        contains the access token provided by the OAuth provider. The
        Patron object represents the authenticated Patron, and the
        PatronData object includes information about the patron
        obtained from the OAuth provider which cannot be stored in the
        circulation manager's database, but which should be passed on
        to the client.
        """
        # Ask the OAuth provider to verify the code that was passed
        # in.  This will give us a bearer token we can use to look up
        # detailed patron information.
        code = params.get('code')
        token = self.remote_exchange_code_for_bearer_token(code)
        if isinstance(token, ProblemDetail):
            return token
        
        # Now that we have a bearer token, use it to look up patron
        # information.
        patrondata = self.remote_patron_lookup(token)
        if isinstance(patrondata, ProblemDetail):
            return patrondata
        
        # Convert the PatronData into a Patron object.
        patron, is_new = patrondata.get_or_create_patron(_db)

        # Create a credential for the Patron.
        credential, is_new = self.create_token(_db, patron, token)
        return credential, patron, patrondata

    # End implementations of OAuthAuthenticationProvider abstract
    # methods.
    
    def remote_exchange_code_for_bearer_token(self, code):
        """Ask the OAuth provider to convert a code (passed in to the OAuth
        callback) into a bearer token.

        We can use the bearer token to act on behalf of a specific
        patron. It also gives us confidence that the patron
        authenticated correctly with Clever.

        :return: A ProblemDetail if there's a problem; otherwise, the
        bearer token.
        """
        payload = dict(
            code=code,
            grant_type='authorization_code',
            redirect_uri=self._server_redirect_uri(),
        )
        authorization = base64.b64encode(
            self.client_id + ":" + self.client_secret
        )
        headers = {
            'Authorization': 'Basic %s' % authorization,
            'Content-Type': 'application/json',
        }
        response = self._get_token(payload, headers)
        invalid = INVALID_CREDENTIALS.detailed(
            _("A valid Clever login is required.")
        )
        if not response:
            return invalid
        token = response.get('access_token', None)
        if not token:
            return invalid
        return token
        
    def remote_patron_lookup(self, token):
        """Use a bearer token to look up detailed patron information.

        :return: A ProblemDetail if there's a problem. Otherwise, a
        PatronData.
        """
        bearer_headers = {
            'Authorization': 'Bearer %s' % token
        }
        result = self._get(self.CLEVER_API_BASE_URL + '/me', bearer_headers)
        data = result.get('data', {})

        identifier = data.get('id', None)

        if not identifier:
            return INVALID_CREDENTIALS.detailed(
                _("A valid Clever login is required.")
            )

        if result.get('type') not in self.SUPPORTED_USER_TYPES:
            return UNSUPPORTED_CLEVER_USER_TYPE

        links = result['links']

        user_link = [l for l in links if l['rel'] == 'canonical'][0]['uri']
        user = self._get(self.CLEVER_API_BASE_URL + user_link, bearer_headers)
        
        user_data = user['data']
        school_id = user_data['school']
        school = self._get(
            self.CLEVER_API_BASE_URL + '/v1.1/schools/%s' % school_id,
            bearer_headers
        )

        school_nces_id = school['data'].get('nces_id')

        # TODO: check student free and reduced lunch status as well

        if school_nces_id not in TITLE_I_NCES_IDS:
            self.log.info("%s didn't match a Title I NCES ID" % school_nces_id)
            return CLEVER_NOT_ELIGIBLE

        if result['type'] == 'student':
            grade = user_data.get('grade')
            external_type = None
            if grade in ["Kindergarten", "1", "2", "3"]:
                external_type = "E"
            elif grade in ["4", "5", "6", "7", "8"]:
                external_type = "M"
            elif grade in ["9", "10", "11", "12"]:
                external_type = "H"
        else:
            external_type = "A"

        patrondata = PatronData(
            permanent_id=identifier,
            authorization_identifier=identifier,
            external_type=external_type,
            personal_name = user_data.get('name'),
            complete=True
        )
        return patrondata
   
    def _server_redirect_uri(self):
        return url_for('oauth_callback', _external=True)

    def _get_token(self, payload, headers):
        response = HTTP.post_with_timeout(
            self.CLEVER_TOKEN_URL, json.dumps(payload), headers=headers
        )
        return response.json()

    def _get(self, url, headers):
        return HTTP.get_with_timeout(url, headers=headers).json()

AuthenticationProvider = CleverAuthenticationAPI
