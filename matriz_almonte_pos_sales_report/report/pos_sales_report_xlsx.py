# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Parser XLSX del Diario de Ventas del POS.

Genera una fila por cada combinación de (forma de pago, tipo de IVA)
presente en cada ticket:

* Si el ticket tiene una única forma de pago y un único tipo de IVA,
  se genera una sola fila.
* Si tiene varias formas de pago (pago mixto) y/o varios tipos de IVA,
  se generan tantas filas como combinaciones existan, prorrateando el
  Neto y la Cuota de IVA de cada tipo impositivo según el peso de cada
  forma de pago sobre el total del ticket. Así, la suma de todas las
  filas de un mismo ticket siempre cuadra exactamente con sus importes
  reales (Neto, IVA y Total).
"""
from collections import defaultdict

from odoo import fields, models


class PosSalesReportXlsx(models.AbstractModel):
    _name = "report.matriz_almonte_pos_sales_report.pos_sales_report_xlsx"
    _description = "Parser del Diario de Ventas del POS en Excel"
    _inherit = "report.report_xlsx.abstract"

    _HEADERS = [
        "Número de la venta",
        "Fecha de la venta",
        "Forma de pago",
        "% IVA",
        "Neto",
        "Importe de IVA",
        "Importe total del ticket",
    ]
    _COLUMN_WIDTHS = [20, 14, 20, 10, 14, 16, 20]

    def generate_xlsx_report(self, workbook, data, wizards):
        for wizard in wizards:
            self._generate_worksheet(workbook, wizard)

    # ------------------------------------------------------------------
    # Formatos y cabecera
    # ------------------------------------------------------------------
    def _add_formats(self, workbook):
        header_format = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#D9D9D9",
                "border": 1,
                "align": "center",
                "valign": "vcenter",
            }
        )
        return {
            "header": header_format,
            "date": workbook.add_format({"num_format": "dd/mm/yyyy", "border": 1}),
            "amount": workbook.add_format({"num_format": "#,##0.00", "border": 1}),
            "percent": workbook.add_format({"num_format": "0.00", "border": 1}),
            "text": workbook.add_format({"border": 1}),
        }

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------
    def _get_lines_by_tax_rate(self, order):
        """Agrupa las líneas del pedido por tipo de IVA (%).

        Devuelve una lista ordenada de tuplas ``(tipo_iva, base,
        cuota)``, una por cada tipo impositivo distinto presente en el
        pedido (0.0 para líneas exentas/sin impuesto).
        """
        groups = defaultdict(lambda: [0.0, 0.0])
        for line in order.lines:
            taxes = line.tax_ids.filtered(lambda t: t.amount_type == "percent")
            rate = taxes[:1].amount if taxes else 0.0
            groups[rate][0] += line.price_subtotal
            groups[rate][1] += line.price_subtotal_incl - line.price_subtotal
        if not groups:
            groups[0.0] = [order.amount_total, 0.0]
        return sorted((rate, base, cuota) for rate, (base, cuota) in groups.items())

    def _get_payments(self, order):
        """Lista de tuplas ``(forma_de_pago, importe)`` del pedido.

        Si el pedido no tiene pagos registrados (estados anómalos), se
        devuelve una única forma de pago vacía por el importe total,
        para no perder la venta en el diario.
        """
        payments = [
            (payment.payment_method_id.name or "", payment.amount)
            for payment in order.payment_ids
        ]
        if not payments:
            payments = [("", order.amount_total)]
        return payments

    def _write_order_rows(self, sheet, fmt, wizard, order, row):
        numero_venta = order.pos_reference or order.name or ""
        local_date = False
        if order.date_order:
            local_date = fields.Datetime.context_timestamp(
                wizard, order.date_order
            ).date()

        tax_groups = self._get_lines_by_tax_rate(order)
        payments = self._get_payments(order)
        total_pagos = sum(amount for _metodo, amount in payments)

        for metodo, importe_pago in payments:
            if total_pagos:
                peso = importe_pago / total_pagos
            else:
                peso = 1.0 / len(payments) if payments else 0.0

            for tax_rate, base, cuota in tax_groups:
                neto = base * peso
                iva = cuota * peso
                total_linea = neto + iva

                sheet.write(row, 0, numero_venta, fmt["text"])
                if local_date:
                    sheet.write_datetime(row, 1, local_date, fmt["date"])
                else:
                    sheet.write(row, 1, "", fmt["text"])
                sheet.write(row, 2, metodo, fmt["text"])
                sheet.write_number(row, 3, tax_rate, fmt["percent"])
                sheet.write_number(row, 4, neto, fmt["amount"])
                sheet.write_number(row, 5, iva, fmt["amount"])
                sheet.write_number(row, 6, total_linea, fmt["amount"])
                row += 1
        return row

    def _generate_worksheet(self, workbook, wizard):
        sheet = workbook.add_worksheet("Diario de Ventas")
        fmt = self._add_formats(workbook)

        for col, header in enumerate(self._HEADERS):
            sheet.write(0, col, header, fmt["header"])

        row = 1
        for order in wizard._get_pos_orders():
            row = self._write_order_rows(sheet, fmt, wizard, order, row)

        for col, width in enumerate(self._COLUMN_WIDTHS):
            sheet.set_column(col, col, width)

        sheet.freeze_panes(1, 0)
        if row > 1:
            sheet.autofilter(0, 0, row - 1, len(self._HEADERS) - 1)

