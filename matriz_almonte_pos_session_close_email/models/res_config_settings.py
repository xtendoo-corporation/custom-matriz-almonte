# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Campo relacionado para configurar el email de cierre de sesión desde
Ajustes > Punto de Venta."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_session_closing_email = fields.Char(
        related="pos_config_id.session_closing_email",
        readonly=False,
        string="Email de cierre de sesión",
    )

