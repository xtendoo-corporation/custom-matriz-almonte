# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MatrizAlmontePosSalesReportWizard(models.TransientModel):
    _name = "matriz_almonte_pos_sales_report_wizard"
    _description = "Informe de ventas POS (Excel)"

    date_from = fields.Date(
        string="Fecha desde",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_to = fields.Date(
        string="Fecha hasta",
        required=True,
        default=fields.Date.context_today,
    )
    all_pos_config = fields.Boolean(
        string="Todos los puntos de venta",
        default=True,
    )
    pos_config_ids = fields.Many2many(
        comodel_name="pos.config",
        string="Puntos de venta",
    )

    @api.onchange("all_pos_config")
    def _onchange_all_pos_config(self):
        if self.all_pos_config:
            self.pos_config_ids = [(5, 0, 0)]

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wizard in self:
            if (
                wizard.date_from
                and wizard.date_to
                and wizard.date_from > wizard.date_to
            ):
                raise ValidationError(
                    _("La fecha 'Desde' no puede ser posterior a la fecha 'Hasta'.")
                )

    @api.constrains("all_pos_config", "pos_config_ids")
    def _check_pos_config(self):
        for wizard in self:
            if not wizard.all_pos_config and not wizard.pos_config_ids:
                raise ValidationError(
                    _(
                        "Debe seleccionar al menos un punto de venta, o bien "
                        "marcar la opción 'Todos los puntos de venta'."
                    )
                )

    def _get_utc_datetime_range(self):
        """Rango UTC (inicio, fin) equivalente al día completo, en la
        zona horaria del usuario, de ``date_from`` a ``date_to``.

        ``pos.order.date_order`` se almacena en UTC; como ``date_from``/
        ``date_to`` son fechas (sin hora) tal como las ve el usuario, hay
        que localizarlas en su zona horaria y convertirlas a UTC antes de
        usarlas en el dominio de búsqueda, para que el filtro cubra
        exactamente el día calendario del usuario.
        """
        self.ensure_one()
        tz_name = self.env.user.tz or "UTC"
        tz = pytz.timezone(tz_name)
        date_from = datetime.combine(self.date_from, time.min)
        date_to = datetime.combine(self.date_to, time.max)
        date_from_utc = tz.localize(date_from).astimezone(pytz.UTC).replace(tzinfo=None)
        date_to_utc = tz.localize(date_to).astimezone(pytz.UTC).replace(tzinfo=None)
        return date_from_utc, date_to_utc

    def _get_pos_order_domain(self):
        self.ensure_one()
        date_from_utc, date_to_utc = self._get_utc_datetime_range()
        domain = [
            ("date_order", ">=", fields.Datetime.to_string(date_from_utc)),
            ("date_order", "<=", fields.Datetime.to_string(date_to_utc)),
            ("state", "!=", "cancel"),
        ]
        if not self.all_pos_config and self.pos_config_ids:
            domain.append(("config_id", "in", self.pos_config_ids.ids))
        return domain

    def _get_pos_orders(self):
        self.ensure_one()
        return self.env["pos.order"].search(
            self._get_pos_order_domain(), order="date_order asc"
        )

    def action_export_xlsx(self):
        self.ensure_one()
        report = self.env.ref(
            "matriz_almonte_pos_sales_report.action_report_pos_sales_xlsx"
        )
        return report.report_action(self.ids)

