# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Parser XLSX del resumen de ventas del POS totalizado por punto de venta.

Genera un bloque por cada combinación de (fecha, punto de venta), con
una matriz que cruza el tipo de IVA (filas) con la forma de pago
(columnas), incluyendo los totales por fila y por columna, al estilo
de un cierre de caja diario::

    Resumen del 20/08/2026              (A/000001 / A/000098)
    PUNTO DE VENTA: DESPACHO 1
    IVA        Efectivo    Tarjeta     Total
    0%         13,50 €     4,50 €      18,00 €
    21%        18,50 €     0,00 €      18,50 €
    TOTAL      32,00 €     4,50 €      36,50 €

El importe de cada celda es el importe realmente cobrado (base + cuota
de IVA) para esa combinación de tipo de IVA y forma de pago.

Cuando un ticket combina varias formas de pago (pago mixto) y/o varios
tipos de IVA, su importe se reparte proporcionalmente entre las
combinaciones correspondientes según el peso de cada forma de pago
sobre el total del ticket, de forma que la suma de todas las celdas de
un bloque siempre cuadra exactamente con la suma de los importes
totales de los pedidos incluidos en él.
"""
from collections import defaultdict

from odoo import fields, models

# Formas de pago que, de existir, se muestran siempre en primer lugar y
# en este orden. La comprobación es por subcadena (no exige coincidencia
# exacta), ya que en la práctica cada punto de venta suele tener su
# propio método de pago en efectivo con un nombre distinto (p. ej.
# "Efectivo Tabaiba", "Efectivo Sonneland"...). El resto de formas de
# pago encontradas se añaden después, por orden alfabético.
_PAYMENT_METHOD_PRIORITY = ["efectivo", "tarjeta"]


class PosSalesReportXlsx(models.AbstractModel):
    _name = "report.matriz_almonte_pos_sales_report.pos_sales_report_xlsx"
    _description = "Parser del resumen de ventas del POS por punto de venta"
    _inherit = "report.report_xlsx.abstract"

    def generate_xlsx_report(self, workbook, data, wizards):
        for wizard in wizards:
            self._generate_worksheet(workbook, wizard)

    # ------------------------------------------------------------------
    # Formatos
    # ------------------------------------------------------------------
    def _add_formats(self, workbook):
        return {
            "title": workbook.add_format({"bold": True, "font_size": 12}),
            "subtitle": workbook.add_format({"bold": True, "font_size": 11}),
            "header": workbook.add_format(
                {
                    "bold": True,
                    "bg_color": "#D9D9D9",
                    "border": 1,
                    "align": "center",
                    "valign": "vcenter",
                }
            ),
            "row_label": workbook.add_format(
                {"bold": True, "border": 1, "align": "center", "valign": "vcenter"}
            ),
            "total_row_label": workbook.add_format(
                {
                    "bold": True,
                    "border": 1,
                    "align": "center",
                    "valign": "vcenter",
                    "bg_color": "#D9D9D9",
                }
            ),
            "amount": workbook.add_format({"num_format": '#,##0.00" €"', "border": 1}),
            "total_amount": workbook.add_format(
                {
                    "num_format": '#,##0.00" €"',
                    "border": 1,
                    "bold": True,
                    "bg_color": "#D9D9D9",
                }
            ),
        }

    # ------------------------------------------------------------------
    # Datos por pedido
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
        para no perder la venta del resumen.
        """
        payments = [
            (payment.payment_method_id.name or "", payment.amount)
            for payment in order.payment_ids
        ]
        if not payments:
            payments = [("", order.amount_total)]
        return payments

    def _get_payment_method_names(self, orders):
        """Conjunto de formas de pago presentes en ``orders``."""
        methods = set()
        for order in orders:
            for metodo, _importe in self._get_payments(order):
                methods.add(metodo)
        return methods

    def _sort_payment_methods(self, methods):
        def _key(name):
            lower = (name or "").strip().lower()
            for idx, keyword in enumerate(_PAYMENT_METHOD_PRIORITY):
                if keyword in lower:
                    return (0, idx, lower)
            return (1, 0, lower)

        return sorted(methods, key=_key)

    def _format_tax_rate(self, rate):
        if float(rate).is_integer():
            return f"{int(rate)}%"
        return f"{rate:.2f}%".replace(".", ",")

    def _get_order_range_label(self, orders):
        if not orders:
            return ""
        refs = [
            order.pos_reference or order.name or ""
            for order in orders.sorted("date_order")
        ]
        refs = [ref for ref in refs if ref]
        if not refs:
            return ""
        if refs[0] == refs[-1]:
            return f"({refs[0]})"
        return f"({refs[0]} / {refs[-1]})"

    # ------------------------------------------------------------------
    # Matriz IVA x forma de pago
    # ------------------------------------------------------------------
    def _build_matrix(self, orders):
        """Matriz ``{tipo_iva: {forma_pago: importe}}`` (IVA incluido)
        para el conjunto de pedidos indicado, prorrateando el importe
        de cada tipo de IVA entre las formas de pago del ticket según
        su peso sobre el total.
        """
        matrix = defaultdict(lambda: defaultdict(float))
        for order in orders:
            tax_groups = self._get_lines_by_tax_rate(order)
            payments = self._get_payments(order)
            total_pagos = sum(amount for _metodo, amount in payments)
            for metodo, importe_pago in payments:
                peso = importe_pago / total_pagos if total_pagos else 1.0 / len(payments)
                for tax_rate, base, cuota in tax_groups:
                    matrix[tax_rate][metodo] += (base + cuota) * peso
        return matrix

    # ------------------------------------------------------------------
    # Bloques (fecha, punto de venta)
    # ------------------------------------------------------------------
    def _get_blocks(self, wizard, orders):
        """Agrupa los pedidos por (fecha local, punto de venta).

        Devuelve una lista ordenada de tuplas ``(fecha, pos_config,
        pedidos)``.
        """
        buckets = defaultdict(lambda: self.env["pos.order"])
        for order in orders:
            local_date = (
                fields.Datetime.context_timestamp(wizard, order.date_order).date()
                if order.date_order
                else False
            )
            buckets[(local_date, order.config_id)] |= order
        blocks = [(key[0], key[1], recs) for key, recs in buckets.items()]
        blocks.sort(
            key=lambda block: (
                block[0] or fields.Date.context_today(wizard),
                block[1].name or "",
            )
        )
        return blocks

    # ------------------------------------------------------------------
    # Escritura de cada bloque
    # ------------------------------------------------------------------
    def _write_block(self, sheet, fmt, row, local_date, config, block_orders, methods):
        n_cols = len(methods) + 2  # IVA + formas de pago + Total
        last_col = n_cols - 1

        date_label = local_date.strftime("%d/%m/%Y") if local_date else "-"
        title_end_col = max(last_col - 1, 0)
        if title_end_col > 0:
            sheet.merge_range(
                row, 0, row, title_end_col, f"Resumen del {date_label}", fmt["title"]
            )
        else:
            sheet.write(row, 0, f"Resumen del {date_label}", fmt["title"])
        range_label = self._get_order_range_label(block_orders)
        if range_label:
            sheet.write(row, last_col, range_label, fmt["title"])
        row += 1

        sheet.merge_range(
            row,
            0,
            row,
            last_col,
            f"PUNTO DE VENTA: {(config.name or '').upper()}",
            fmt["subtitle"],
        )
        row += 1

        sheet.write(row, 0, "IVA", fmt["header"])
        for col, metodo in enumerate(methods, start=1):
            sheet.write(row, col, metodo or "Sin forma de pago", fmt["header"])
        sheet.write(row, last_col, "Total", fmt["header"])
        row += 1

        matrix = self._build_matrix(block_orders)
        col_totals = defaultdict(float)
        grand_total = 0.0
        for tax_rate in sorted(matrix.keys()):
            sheet.write(row, 0, self._format_tax_rate(tax_rate), fmt["row_label"])
            row_total = 0.0
            for col, metodo in enumerate(methods, start=1):
                importe = matrix[tax_rate].get(metodo, 0.0)
                sheet.write_number(row, col, importe, fmt["amount"])
                col_totals[metodo] += importe
                row_total += importe
            sheet.write_number(row, last_col, row_total, fmt["amount"])
            grand_total += row_total
            row += 1

        sheet.write(row, 0, "TOTAL", fmt["total_row_label"])
        for col, metodo in enumerate(methods, start=1):
            sheet.write_number(row, col, col_totals.get(metodo, 0.0), fmt["total_amount"])
        sheet.write_number(row, last_col, grand_total, fmt["total_amount"])
        row += 1

        return row + 1  # fila en blanco de separación entre bloques

    def _generate_worksheet(self, workbook, wizard):
        sheet = workbook.add_worksheet("Resumen por punto de venta")
        fmt = self._add_formats(workbook)

        orders = wizard._get_pos_orders()
        methods = self._sort_payment_methods(self._get_payment_method_names(orders))

        sheet.set_column(0, 0, 22)
        for col in range(1, len(methods) + 2):
            sheet.set_column(col, col, 16)

        blocks = self._get_blocks(wizard, orders)
        if not blocks:
            sheet.write(0, 0, "No se han encontrado ventas para el filtro seleccionado.")
            return

        row = 0
        for local_date, config, block_orders in blocks:
            row = self._write_block(
                sheet, fmt, row, local_date, config, block_orders, methods
            )

