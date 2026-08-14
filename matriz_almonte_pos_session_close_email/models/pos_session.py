# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Envío automático por email de la información de cierre de la sesión POS.

Cuando una sesión de Punto de Venta se cierra correctamente, si el TPV tiene
configurada una dirección en ``pos.config.session_closing_email`` se envía a
esa dirección un resumen del cierre (ventas, pagos y efectivo) junto con el
informe estándar de detalle de ventas de la sesión en PDF.
"""

import logging

from markupsafe import Markup

from odoo import _, models

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    _inherit = "pos.session"

    def _validate_session(
        self,
        balancing_account=False,
        amount_to_balance=0,
        bank_payment_method_diffs=None,
    ):
        # Se ejecuta el cierre estándar primero. Si la sesión queda
        # descuadrada, el super devuelve la acción del asistente de cierre y
        # NO marca la sesión como cerrada; en ese caso no se envía el correo.
        result = super()._validate_session(
            balancing_account=balancing_account,
            amount_to_balance=amount_to_balance,
            bank_payment_method_diffs=bank_payment_method_diffs,
        )
        if self.state == "closed":
            # El envío del correo nunca debe impedir el cierre de la sesión.
            try:
                self._matriz_almonte_send_closing_email()
            except Exception:  # noqa: BLE001 - el email no debe romper el cierre
                _logger.exception(
                    "[POS CLOSE EMAIL] Error enviando el email de cierre de "
                    "la sesión %s.",
                    self.name,
                )
        return result

    def _matriz_almonte_send_closing_email(self):
        """Construye y envía el email de cierre a la dirección configurada."""
        self.ensure_one()
        email_to = (self.config_id.session_closing_email or "").strip()
        if not email_to:
            return

        body_html = self._matriz_almonte_build_closing_email_body()
        attachments = self._matriz_almonte_build_closing_email_attachments()

        mail = (
            self.env["mail.mail"]
            .sudo()
            .create(
                {
                    "subject": _(
                        "Cierre de sesión POS: %(name)s", name=self.name
                    ),
                    "email_from": (
                        self.env.company.email
                        or self.env.user.email_formatted
                        or False
                    ),
                    "email_to": email_to,
                    "body_html": body_html,
                    "auto_delete": True,
                    "attachment_ids": [(6, 0, attachments.ids)]
                    if attachments
                    else False,
                }
            )
        )
        mail.send()
        _logger.info(
            "[POS CLOSE EMAIL] Email de cierre de la sesión %s enviado a %s.",
            self.name,
            email_to,
        )

    def _matriz_almonte_build_closing_email_body(self):
        """Genera el cuerpo HTML con el resumen del cierre de la sesión."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id

        def money(amount):
            return currency.format(amount) if currency else "%.2f" % (amount or 0.0)

        orders = self._get_closed_orders()
        orders_qty = len(orders)
        orders_total = sum(orders.mapped("amount_total"))

        # Pagos agrupados por método (excluyendo "pagar después").
        payments = orders.payment_ids.filtered(
            lambda p: p.payment_method_id.type != "pay_later"
        )
        payments_by_method = {}
        for payment in payments:
            method = payment.payment_method_id
            payments_by_method.setdefault(method, 0.0)
            payments_by_method[method] += payment.amount

        opened_at = self.start_at or ""
        closed_at = self.stop_at or ""

        rows = Markup("").join(
            Markup(
                "<tr>"
                "<td style='padding:4px 12px 4px 0;'>%s</td>"
                "<td style='padding:4px 0;text-align:right;'>%s</td>"
                "</tr>"
            )
            % (method.name, money(amount))
            for method, amount in payments_by_method.items()
        )
        payments_table = (
            Markup(
                "<table style='border-collapse:collapse;margin-top:4px;'>%s</table>"
            )
            % rows
            if rows
            else Markup("<p><i>%s</i></p>") % _("Sin pagos registrados.")
        )

        closing_notes = self.closing_notes or ""
        notes_block = (
            Markup("<p><b>%s</b><br/>%s</p>")
            % (_("Notas de cierre:"), Markup.escape(closing_notes))
            if closing_notes
            else Markup("")
        )

        return Markup(
            """
            <div style="font-family:Arial,Helvetica,sans-serif;color:#333;">
                <h2 style="margin-bottom:4px;">%(title)s</h2>
                <p style="margin-top:0;color:#666;">%(config)s</p>
                <table style="border-collapse:collapse;margin-bottom:16px;">
                    <tr><td style="padding:4px 12px 4px 0;"><b>%(session_l)s</b></td>
                        <td style="padding:4px 0;">%(session_v)s</td></tr>
                    <tr><td style="padding:4px 12px 4px 0;"><b>%(user_l)s</b></td>
                        <td style="padding:4px 0;">%(user_v)s</td></tr>
                    <tr><td style="padding:4px 12px 4px 0;"><b>%(opened_l)s</b></td>
                        <td style="padding:4px 0;">%(opened_v)s</td></tr>
                    <tr><td style="padding:4px 12px 4px 0;"><b>%(closed_l)s</b></td>
                        <td style="padding:4px 0;">%(closed_v)s</td></tr>
                </table>

                <h3 style="margin-bottom:4px;">%(sales_l)s</h3>
                <table style="border-collapse:collapse;margin-bottom:16px;">
                    <tr><td style="padding:4px 12px 4px 0;">%(orders_l)s</td>
                        <td style="padding:4px 0;text-align:right;">%(orders_v)s</td></tr>
                    <tr><td style="padding:4px 12px 4px 0;"><b>%(total_l)s</b></td>
                        <td style="padding:4px 0;text-align:right;"><b>%(total_v)s</b></td></tr>
                </table>

                <h3 style="margin-bottom:4px;">%(payments_l)s</h3>
                %(payments_table)s

                <h3 style="margin-bottom:4px;margin-top:16px;">%(cash_l)s</h3>
                <table style="border-collapse:collapse;margin-bottom:16px;">
                    <tr><td style="padding:4px 12px 4px 0;">%(cash_start_l)s</td>
                        <td style="padding:4px 0;text-align:right;">%(cash_start_v)s</td></tr>
                    <tr><td style="padding:4px 12px 4px 0;">%(cash_end_l)s</td>
                        <td style="padding:4px 0;text-align:right;">%(cash_end_v)s</td></tr>
                    <tr><td style="padding:4px 12px 4px 0;">%(cash_diff_l)s</td>
                        <td style="padding:4px 0;text-align:right;">%(cash_diff_v)s</td></tr>
                </table>

                %(notes_block)s

                <p style="color:#999;font-size:12px;margin-top:24px;">
                    %(footer)s
                </p>
            </div>
            """
        ) % {
            "title": _("Resumen de cierre de sesión"),
            "config": self.config_id.display_name or "",
            "session_l": _("Sesión"),
            "session_v": self.name or "",
            "user_l": _("Responsable"),
            "user_v": self.user_id.name or "",
            "opened_l": _("Apertura"),
            "opened_v": opened_at,
            "closed_l": _("Cierre"),
            "closed_v": closed_at,
            "sales_l": _("Ventas"),
            "orders_l": _("Número de pedidos"),
            "orders_v": orders_qty,
            "total_l": _("Total vendido"),
            "total_v": money(orders_total),
            "payments_l": _("Pagos por método"),
            "payments_table": payments_table,
            "cash_l": _("Efectivo"),
            "cash_start_l": _("Saldo inicial"),
            "cash_start_v": money(self.cash_register_balance_start),
            "cash_end_l": _("Saldo final contado"),
            "cash_end_v": money(self.cash_register_balance_end_real),
            "cash_diff_l": _("Diferencia"),
            "cash_diff_v": money(self.cash_register_difference),
            "notes_block": notes_block,
            "footer": _(
                "Correo generado automáticamente al cerrar la sesión del "
                "Punto de Venta."
            ),
        }

    def _matriz_almonte_build_closing_email_attachments(self):
        """Renderiza el informe de detalle de ventas de la sesión en PDF.

        Devuelve un recordset ``ir.attachment`` (vacío si no se pudo generar,
        para que el correo se envíe igualmente con el resumen HTML).
        """
        self.ensure_one()
        Attachment = self.env["ir.attachment"].sudo()
        try:
            report = self.env.ref("point_of_sale.sale_details_report")
            pdf_content, _content_type = report.sudo()._render_qweb_pdf(
                "point_of_sale.report_saledetails",
                res_ids=[],
                data={
                    "date_start": False,
                    "date_stop": False,
                    "config_ids": self.config_id.ids,
                    "session_ids": self.ids,
                },
            )
        except Exception:  # noqa: BLE001 - el PDF es opcional
            _logger.exception(
                "[POS CLOSE EMAIL] No se pudo generar el PDF de detalle de "
                "ventas de la sesión %s; se envía solo el resumen.",
                self.name,
            )
            return Attachment.browse()

        filename = "Cierre_%s.pdf" % (self.name or self.id)
        filename = filename.replace("/", "-").replace(" ", "_")
        return Attachment.create(
            {
                "name": filename,
                "type": "binary",
                "raw": pdf_content,
                "mimetype": "application/pdf",
                "res_model": "pos.session",
                "res_id": self.id,
            }
        )

