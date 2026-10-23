# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
from collections import Counter

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install")
class TestReportPosOrderPaymentTax(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_21 = cls._create_sale_tax("21% B test", 21.0)
        cls.tax_0 = cls._create_sale_tax("0% EX S test", 0.0)
        cls.tax_re = cls._create_sale_tax("5.2% RE test", 5.2)
        cls.product_21 = cls.create_product(
            "Product 21", cls.categ_basic, 100.0, tax_ids=cls.tax_21.ids
        )
        cls.product_0 = cls.create_product(
            "Product 0", cls.categ_basic, 50.0, tax_ids=cls.tax_0.ids
        )
        cls.product_21_re = cls.create_product(
            "Product 21 + RE",
            cls.categ_basic,
            100.0,
            tax_ids=(cls.tax_21 | cls.tax_re).ids,
        )
        cls.product_no_tax = cls.create_product("Product no tax", cls.categ_basic, 30.0)

    @classmethod
    def _create_sale_tax(cls, name, amount):
        return cls.env["account.tax"].create(
            {
                "name": name,
                "amount": amount,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include_override": "tax_excluded",
                "company_id": cls.env.company.id,
            }
        )

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.config.write(
            {"payment_method_ids": [Command.set((self.cash_pm1 | self.bank_pm1).ids)]}
        )
        self.open_new_session()

    # Helpers
    def _create_order(self, lines, payments):
        orders = self._create_orders(
            [{"pos_order_lines_ui_args": lines, "payments": payments}]
        )
        return next(iter(orders.values()))

    def _report_rows(self, order):
        return self.env["report.pos.order"].search([("order_id", "=", order.id)])

    def _sum(self, rows, field_name, **filters):
        return sum(
            row[field_name]
            for row in rows
            if all(row[key] == value for key, value in filters.items())
        )

    def _assert_totals_match_order(self, order, rows):
        self.assertAlmostEqual(
            self._sum(rows, "price_subtotal_excl"),
            sum(order.lines.mapped("price_subtotal")),
            places=2,
        )
        self.assertAlmostEqual(
            self._sum(rows, "tax_amount"), order.amount_tax, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "price_total"), order.amount_total, places=2
        )
        self.assertEqual(self._sum(rows, "nbr_lines"), len(order.lines))
        self.assertEqual(self._sum(rows, "product_qty"), sum(order.lines.mapped("qty")))

    def _assert_no_duplicates(self, rows):
        self.assertEqual(len(set(rows.ids)), len(rows))
        keys = Counter(
            (row.id // 10000, row.payment_method_id.id, row.tax_id.id) for row in rows
        )
        self.assertFalse([key for key, count in keys.items() if count > 1])

    # Tests
    def test_mixed_payment_two_tax_rates(self):
        """Cash + card order with a 21% and a 0% line is split without
        duplicating amounts: totals per method match the payments and
        totals per tax match the order lines."""
        order = self._create_order(
            [(self.product_21, 1), (self.product_0, 1)],
            [(self.cash_pm1, 100.0), (self.bank_pm1, 71.0)],
        )
        self.assertAlmostEqual(order.amount_total, 171.0)
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 4)
        self._assert_no_duplicates(rows)
        self._assert_totals_match_order(order, rows)

        cash, bank = self.cash_pm1, self.bank_pm1
        self.assertAlmostEqual(
            self._sum(rows, "price_total", payment_method_id=cash), 100.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "price_total", payment_method_id=bank), 71.0, places=2
        )
        for row in rows.filtered(lambda r: r.payment_method_id == cash):
            self.assertAlmostEqual(row.payment_share, 100.0 / 171.0, places=6)

        self.assertAlmostEqual(
            self._sum(rows, "price_subtotal_excl", tax_id=self.tax_21), 100.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "tax_amount", tax_id=self.tax_21), 21.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "price_subtotal_excl", tax_id=self.tax_0), 50.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "tax_amount", tax_id=self.tax_0), 0.0, places=2
        )
        for row in rows:
            self.assertAlmostEqual(
                row.price_total, row.price_subtotal_excl + row.tax_amount, places=6
            )

    def test_read_group_by_payment_method_and_tax(self):
        """Grouping the report as the pivot does returns consistent sums."""
        order = self._create_order(
            [(self.product_21, 2), (self.product_0, 1)],
            [(self.cash_pm1, 150.0), (self.bank_pm1, 142.0)],
        )
        groups = self.env["report.pos.order"]._read_group(
            [("order_id", "=", order.id)],
            ["payment_method_id", "tax_id"],
            ["price_subtotal_excl:sum", "tax_amount:sum", "price_total:sum"],
        )
        self.assertEqual(len(groups), 4)
        by_method = Counter()
        for method, _tax, base, tax, total in groups:
            by_method[method] += total
            self.assertAlmostEqual(base + tax, total, places=6)
        self.assertAlmostEqual(by_method[self.cash_pm1], 150.0, places=2)
        self.assertAlmostEqual(by_method[self.bank_pm1], 142.0, places=2)

    def test_single_payment(self):
        """A single payment keeps one row per line with the whole amount."""
        order = self._create_order(
            [(self.product_21, 1), (self.product_0, 2)],
            [(self.bank_pm1, 221.0)],
        )
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 2)
        self._assert_no_duplicates(rows)
        self._assert_totals_match_order(order, rows)
        self.assertEqual(rows.payment_method_id, self.bank_pm1)
        self.assertEqual(set(rows.mapped("payment_share")), {1.0})
        self.assertEqual(rows.tax_id, self.tax_21 | self.tax_0)

    def test_cash_change_is_netted(self):
        """The cash change (negative payment) reduces the cash share."""
        order = self._create_order(
            [(self.product_21, 1), (self.product_0, 1)],
            [(self.cash_pm1, 200.0), (self.cash_pm1, -29.0)],
        )
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.payment_method_id, self.cash_pm1)
        self._assert_totals_match_order(order, rows)

    def test_line_with_several_taxes(self):
        """A line with two taxes yields one row per tax: the base stays on
        the first tax and the tax amount is split by rate."""
        order = self._create_order(
            [(self.product_21_re, 1)],
            [(self.cash_pm1, 126.2)],
        )
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 2)
        self._assert_no_duplicates(rows)
        self._assert_totals_match_order(order, rows)
        self.assertAlmostEqual(
            self._sum(rows, "price_subtotal_excl", tax_id=self.tax_21), 100.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "tax_amount", tax_id=self.tax_21), 21.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "price_subtotal_excl", tax_id=self.tax_re), 0.0, places=2
        )
        self.assertAlmostEqual(
            self._sum(rows, "tax_amount", tax_id=self.tax_re), 5.2, places=2
        )
        self.assertEqual(self._sum(rows, "product_qty", tax_id=self.tax_re), 0)

    def test_line_without_taxes(self):
        """A line without taxes keeps a single row with an empty tax."""
        order = self._create_order(
            [(self.product_no_tax, 1)],
            [(self.cash_pm1, 10.0), (self.bank_pm1, 20.0)],
        )
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 2)
        self.assertFalse(rows.tax_id)
        self._assert_totals_match_order(order, rows)
        self.assertAlmostEqual(
            self._sum(rows, "price_total", payment_method_id=self.bank_pm1),
            20.0,
            places=2,
        )

    def _create_draft_order(self):
        return self.env["pos.order"].create(
            {
                "session_id": self.pos_session.id,
                "lines": [
                    Command.create(
                        {
                            "name": "OL/0001",
                            "product_id": self.product_21.id,
                            "price_unit": 100.0,
                            "qty": 1.0,
                            "tax_ids": [Command.set(self.tax_21.ids)],
                            "price_subtotal": 100.0,
                            "price_subtotal_incl": 121.0,
                        }
                    )
                ],
                "amount_total": 121.0,
                "amount_tax": 21.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )

    def test_order_without_payments(self):
        """Unpaid orders keep the whole amount with an empty payment method."""
        order = self._create_draft_order()
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows.payment_method_id)
        self.assertEqual(rows.payment_share, 1.0)
        self._assert_totals_match_order(order, rows)

    def test_zero_paid_total_split_evenly(self):
        """When the net paid total is zero, methods share the order evenly
        instead of dividing by zero."""
        order = self._create_draft_order()
        for method, amount in ((self.cash_pm1, 10.0), (self.bank_pm1, -10.0)):
            self.env["pos.payment"].create(
                {
                    "pos_order_id": order.id,
                    "payment_method_id": method.id,
                    "amount": amount,
                }
            )
        rows = self._report_rows(order)

        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows.mapped("payment_share")), {0.5})
        self._assert_totals_match_order(order, rows)

    def test_shared_favorite(self):
        favorite = self.env.ref(
            "matriz_almonte_pos_report_payment_tax."
            "filter_sales_by_payment_method_date_tax"
        )
        self.assertFalse(favorite.user_ids)
        self.assertEqual(favorite.model_id, "report.pos.order")
        self.assertIn("'pivot_column_groupby': ['tax_id']", favorite.context)
