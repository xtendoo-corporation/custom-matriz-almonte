# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestResConfigSettings(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env.company.email = "odoo@example.com"
        pos_config = self.env["pos.config"].create(
            {"name": "Test POS Email", "company_id": self.env.company.id}
        )
        self.settings = self.env["res.config.settings"].create(
            {
                "pos_config_id": pos_config.id,
                "pos_session_closing_email": "cierres@example.com",
            }
        )

    def test_send_test_closing_email(self):
        with patch.object(type(self.env["mail.mail"]), "send") as send:
            result = self.settings.action_send_test_closing_email()

        send.assert_called_once()
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "success")

    def test_send_test_closing_email_without_recipient(self):
        self.settings.pos_session_closing_email = False

        with self.assertRaises(UserError):
            self.settings.action_send_test_closing_email()
