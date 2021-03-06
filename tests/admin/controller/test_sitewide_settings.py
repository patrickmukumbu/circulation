from nose.tools import (
    set_trace,
    eq_,
    assert_raises
)
from api.admin.exceptions import *
from api.config import Configuration
from core.opds import AcquisitionFeed
from core.model import (
    AdminRole,
    ConfigurationSetting
)
from test_controller import SettingsControllerTest
from werkzeug import ImmutableMultiDict, MultiDict
import flask

class TestSitewideSettings(SettingsControllerTest):

    def test_sitewide_settings_get(self):
        with self.request_context_with_admin("/"):
            response = self.manager.admin_sitewide_configuration_settings_controller.process_get()
            settings = response.get("settings")
            all_settings = response.get("all_settings")

            eq_([], settings)
            keys = [s.get("key") for s in all_settings]
            assert AcquisitionFeed.GROUPED_MAX_AGE_POLICY in keys
            assert AcquisitionFeed.NONGROUPED_MAX_AGE_POLICY in keys
            assert Configuration.SECRET_KEY in keys

        ConfigurationSetting.sitewide(self._db, AcquisitionFeed.GROUPED_MAX_AGE_POLICY).value = 0
        ConfigurationSetting.sitewide(self._db, Configuration.SECRET_KEY).value = "secret"
        self._db.flush()

        with self.request_context_with_admin("/"):
            response = self.manager.admin_sitewide_configuration_settings_controller.process_get()
            settings = response.get("settings")
            all_settings = response.get("all_settings")

            eq_(2, len(settings))
            settings_by_key = { s.get("key") : s.get("value") for s in settings }
            eq_("0", settings_by_key.get(AcquisitionFeed.GROUPED_MAX_AGE_POLICY))
            eq_("secret", settings_by_key.get(Configuration.SECRET_KEY))
            keys = [s.get("key") for s in all_settings]
            assert AcquisitionFeed.GROUPED_MAX_AGE_POLICY in keys
            assert AcquisitionFeed.NONGROUPED_MAX_AGE_POLICY in keys
            assert Configuration.SECRET_KEY in keys

            self.admin.remove_role(AdminRole.SYSTEM_ADMIN)
            self._db.flush()
            assert_raises(AdminNotAuthorized,
                          self.manager.admin_sitewide_configuration_settings_controller.process_get)

    def test_sitewide_settings_post_errors(self):
        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([("key", None)])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response, MISSING_SITEWIDE_SETTING_KEY)

        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", Configuration.SECRET_KEY),
                ("value", None)
            ])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response, MISSING_SITEWIDE_SETTING_VALUE)

        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", Configuration.BASE_URL_KEY),
                ("value", "bad_url")
            ])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response.uri, INVALID_URL.uri)
            assert "bad_url" in response.detail

        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", Configuration.GROUPED_MAX_AGE_POLICY),
                ("value", "not a number!")
            ])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response.uri, INVALID_NUMBER.uri)
            assert "not a number!" in response.detail

        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", Configuration.STATIC_FILE_CACHE_TIME),
                ("value", "-5")
            ])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response.uri, INVALID_NUMBER.uri)
            eq_("Cache time for static images and JS and CSS files (in seconds) must be greater than 0.", response.detail)

        self.admin.remove_role(AdminRole.SYSTEM_ADMIN)
        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", Configuration.SECRET_KEY),
                ("value", "secret"),
            ])
            assert_raises(AdminNotAuthorized,
                          self.manager.admin_sitewide_configuration_settings_controller.process_post)

    def test_sitewide_settings_post_create(self):
        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", AcquisitionFeed.GROUPED_MAX_AGE_POLICY),
                ("value", "10"),
            ])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response.status_code, 200)

        # The setting was created.
        setting = ConfigurationSetting.sitewide(self._db, AcquisitionFeed.GROUPED_MAX_AGE_POLICY)
        eq_(setting.key, response.response[0])
        eq_("10", setting.value)

    def test_sitewide_settings_post_edit(self):
        setting = ConfigurationSetting.sitewide(self._db, AcquisitionFeed.GROUPED_MAX_AGE_POLICY)
        setting.value = "10"

        with self.request_context_with_admin("/", method="POST"):
            flask.request.form = MultiDict([
                ("key", AcquisitionFeed.GROUPED_MAX_AGE_POLICY),
                ("value", "20"),
            ])
            response = self.manager.admin_sitewide_configuration_settings_controller.process_post()
            eq_(response.status_code, 200)

        # The setting was changed.
        eq_(setting.key, response.response[0])
        eq_("20", setting.value)

    def test_sitewide_setting_delete(self):
        setting = ConfigurationSetting.sitewide(self._db, AcquisitionFeed.GROUPED_MAX_AGE_POLICY)
        setting.value = "10"

        with self.request_context_with_admin("/", method="DELETE"):
            self.admin.remove_role(AdminRole.SYSTEM_ADMIN)
            assert_raises(AdminNotAuthorized,
                          self.manager.admin_sitewide_configuration_settings_controller.process_delete,
                          setting.key)

            self.admin.add_role(AdminRole.SYSTEM_ADMIN)
            response = self.manager.admin_sitewide_configuration_settings_controller.process_delete(setting.key)
            eq_(response.status_code, 200)

        eq_(None, setting.value)
