# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Configuración del email de cierre de sesión desde Ajustes > Punto de Venta."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_session_closing_email = fields.Char(
        related="pos_config_id.session_closing_email",
        readonly=False,
        string="Email de cierre de sesión",
    )

    def action_send_test_closing_email(self):
        """Envía un correo de prueba a las direcciones configuradas."""
        self.ensure_one()
        email_to = (self.pos_session_closing_email or "").strip()
        if not email_to:
            raise UserError(
                _("Configura al menos una dirección de correo antes de probar el envío.")
            )

        email_from = self.env.company.email or self.env.user.email_formatted
        if not email_from:
            raise UserError(
                _(
                    "Configura un correo electrónico para la compañía o para tu "
                    "usuario antes de probar el envío."
                )
            )

        self.env["mail.mail"].create(
            {
                "subject": _("Prueba de email de cierre de sesión POS"),
                "email_from": email_from,
                "email_to": email_to,
                "body_html": _(
                    "<p>Este es un correo de prueba del cierre de sesión del "
                    "Punto de Venta.</p>"
                    "<p>La comunicación de correo está funcionando correctamente.</p>"
                ),
            }
        ).send()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Correo de prueba enviado"),
                "message": _("Se ha enviado el correo a: %s", email_to),
                "type": "success",
                "sticky": False,
            },
        }
