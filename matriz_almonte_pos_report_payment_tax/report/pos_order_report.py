# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from odoo import fields, models, tools

# Upper bounds used to build a stable, unique integer id for every
# (order line, payment method, tax) row: up to 99 payment methods per order
# and 99 taxes per line.
_METHOD_ID_FACTOR = 10000
_TAX_ID_FACTOR = 100


class ReportPosOrder(models.Model):
    """Split the PoS orders analysis by payment method and tax.

    The standard view has one row per order line and attributes the whole
    line to the first payment of the order, so mixed payments are reported
    under a single method. Here the standard query is wrapped and every
    row becomes ``order line x payment method x tax``:

    * Amounts are weighted by ``payment_share`` (net amount paid with the
      method / total paid in the order), so sums by method match the PoS
      payments and sums by any other grouping match the order lines.
    * When a line has several taxes, the untaxed amount (and the rest of
      tax-agnostic measures) stays on the first tax row, while the tax
      amount is split proportionally to each tax rate.
    * Quantities and line counts stay on the first row of the line so they
      are never duplicated.
    """

    _inherit = "report.pos.order"

    tax_id = fields.Many2one("account.tax", string="Tax", readonly=True)
    tax_amount = fields.Float(readonly=True)
    payment_share = fields.Float(readonly=True, digits=(16, 6), aggregator=None)

    def _payment_tax_base_query(self):
        """Standard report query (one row per order line)."""
        return f"{self._select()} {self._from()}"

    def _payment_tax_ctes(self):
        tax_field = self.env["pos.order.line"]._fields["tax_ids"]
        return f"""
            payment_share AS (
                SELECT
                    shares.order_id,
                    shares.payment_method_id,
                    shares.payment_share,
                    ROW_NUMBER() OVER (
                        PARTITION BY shares.order_id
                        ORDER BY shares.payment_method_id
                    ) AS method_rank
                FROM (
                    SELECT
                        method_total.order_id,
                        method_total.payment_method_id,
                        CASE
                            WHEN SUM(method_total.amount) OVER order_window = 0
                            THEN 1.0 / COUNT(*) OVER order_window
                            ELSE method_total.amount
                                / SUM(method_total.amount) OVER order_window
                        END AS payment_share
                    FROM (
                        SELECT
                            payment.pos_order_id AS order_id,
                            payment.payment_method_id,
                            SUM(payment.amount)::numeric AS amount
                        FROM pos_payment payment
                        GROUP BY payment.pos_order_id, payment.payment_method_id
                    ) method_total
                    WINDOW order_window AS (PARTITION BY method_total.order_id)
                ) shares
                WHERE shares.payment_share <> 0
            ),
            line_tax AS (
                SELECT
                    rel."{tax_field.column1}" AS line_id,
                    rel."{tax_field.column2}" AS tax_id,
                    ROW_NUMBER() OVER tax_order AS tax_rank,
                    CASE
                        WHEN SUM(tax.amount) OVER line_window = 0
                        THEN CASE WHEN ROW_NUMBER() OVER tax_order = 1
                            THEN 1.0 ELSE 0.0 END
                        ELSE tax.amount::numeric
                            / SUM(tax.amount::numeric) OVER line_window
                    END AS tax_weight
                FROM "{tax_field.relation}" rel
                JOIN account_tax tax ON tax.id = rel."{tax_field.column2}"
                WINDOW line_window AS (PARTITION BY rel."{tax_field.column1}"),
                    tax_order AS (
                        PARTITION BY rel."{tax_field.column1}"
                        ORDER BY tax.sequence, tax.id
                    )
            )
        """

    def _payment_tax_columns(self):
        """SQL expression of every column that differs from the base query."""
        share = "COALESCE(ps.payment_share, 1.0)"
        first_tax = "(CASE WHEN COALESCE(lt.tax_rank, 1) = 1 THEN 1 ELSE 0 END)"
        first_row = (
            "(CASE WHEN COALESCE(lt.tax_rank, 1) = 1"
            " AND COALESCE(ps.method_rank, 1) = 1 THEN 1 ELSE 0 END)"
        )
        base = f"rb.price_subtotal_excl * {share} * {first_tax}"
        tax = (
            f"(rb.price_total - rb.price_subtotal_excl) * {share}"
            " * COALESCE(lt.tax_weight, 1.0)"
        )
        return {
            "id": (
                f"rb.id::bigint * {_METHOD_ID_FACTOR}"
                f" + COALESCE(ps.method_rank, 0) * {_TAX_ID_FACTOR}"
                " + COALESCE(lt.tax_rank, 0)"
            ),
            "payment_method_id": "ps.payment_method_id",
            "payment_share": share,
            "tax_id": "lt.tax_id",
            "price_subtotal_excl": base,
            "tax_amount": tax,
            "price_total": f"{base} + {tax}",
            "price_sub_total": f"rb.price_sub_total * {share} * {first_tax}",
            "total_discount": f"rb.total_discount * {share} * {first_tax}",
            "margin": f"rb.margin * {share} * {first_tax}",
            "product_qty": f"rb.product_qty * {first_row}",
            "nbr_lines": f"rb.nbr_lines * {first_row}",
        }

    def _payment_tax_query(self):
        columns = self._payment_tax_columns()
        select = [f"{expression} AS {name}" for name, expression in columns.items()]
        select += [
            f'rb."{name}" AS "{name}"'
            for name, field in self._fields.items()
            if field.store and field.column_type and name not in columns
        ]
        select_clause = ",\n                ".join(select)
        return f"""
            WITH {self._payment_tax_ctes()}
            SELECT
                {select_clause}
            FROM ({self._payment_tax_base_query()}) rb
            LEFT JOIN payment_share ps ON ps.order_id = rb.order_id
            LEFT JOIN line_tax lt ON lt.line_id = rb.id
        """

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"CREATE OR REPLACE VIEW {self._table} AS ({self._payment_tax_query()})"
        )
