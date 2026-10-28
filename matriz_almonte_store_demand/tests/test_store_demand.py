from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged

from .common import StoreDemandCommon


@tagged("post_install", "-at_install")
class TestStoreDemand(StoreDemandCommon):
    def test_defaults_from_user_and_central(self):
        demand = self._create_demand(self.user_shop_a, self.shop_a, [])
        new_demand = self.env["matriz.store.demand"].with_user(self.user_shop_a).new({})
        self.assertEqual(new_demand.warehouse_id, self.shop_a)
        self.assertEqual(new_demand.central_warehouse_id, self.central)
        self.assertEqual(demand.user_id, self.user_shop_a)
        self.assertEqual(demand.state, "draft")
        self.assertTrue(demand.name.startswith("PRE/"))

    def test_central_user_has_no_default_shop(self):
        new_demand = self.env["matriz.store.demand"].with_user(self.user_central).new({})
        self.assertFalse(new_demand.warehouse_id)

    def test_shop_must_differ_from_central(self):
        with self.assertRaises(ValidationError):
            self._create_demand(self.user_central, self.central, [])

    def test_single_central_per_company(self):
        with self.assertRaises(ValidationError):
            self.shop_a.is_store_demand_central = True

    def test_line_defaults_vendor_and_stock_info(self):
        self._set_central_stock(self.product_1, 7)
        demand = self._create_demand(
            self.user_shop_a, self.shop_a, [(self.product_1, 3), (self.product_no_vendor, 1)]
        )
        line, line_no_vendor = demand.line_ids
        self.assertEqual(line.partner_id, self.vendor_1)
        self.assertFalse(line_no_vendor.partner_id)
        self.assertEqual(line.central_free_qty, 7)
        self.assertEqual(line.shop_qty_available, 0)
        self.assertEqual(demand.line_count, 2)
        self.assertEqual(demand.total_qty, 4)

    def test_send_validations(self):
        empty = self._create_demand(self.user_shop_a, self.shop_a, [])
        with self.assertRaisesRegex(UserError, "no tiene líneas"):
            empty.action_send()
        zero = self._create_demand(self.user_shop_a, self.shop_a, [(self.product_1, 0)])
        with self.assertRaisesRegex(UserError, "mayor que cero"):
            zero.action_send()
        demand = self._create_demand(
            self.user_shop_a, self.shop_a, [(self.product_1, 2)], send=True
        )
        self.assertEqual(demand.state, "sent")
        self.assertEqual(demand.line_ids.state, "sent")
        with self.assertRaisesRegex(UserError, "Solo se pueden enviar"):
            demand.action_send()

    def test_shop_cannot_edit_sent_demand_but_central_can(self):
        demand = self._create_demand(
            self.user_shop_a, self.shop_a, [(self.product_1, 2)], send=True
        )
        with self.assertRaises(UserError):
            demand.line_ids.product_qty = 5
        with self.assertRaises(UserError):
            demand.write({"line_ids": [Command.create({
                "product_id": self.product_2.id, "product_qty": 1,
            })]})
        with self.assertRaises(UserError):
            demand.line_ids.unlink()
        central_line = demand.line_ids.with_user(self.user_central)
        central_line.product_qty = 5
        central_line.partner_id = self.vendor_2
        self.assertEqual(demand.line_ids.product_qty, 5)
        self.assertEqual(demand.line_ids.partner_id, self.vendor_2)

    def test_draft_cancel_and_unlink(self):
        demand = self._create_demand(
            self.user_shop_a, self.shop_a, [(self.product_1, 2)], send=True
        )
        with self.assertRaisesRegex(UserError, "Solo se pueden eliminar"):
            demand.unlink()
        demand.action_draft()
        self.assertEqual(demand.state, "draft")
        demand.line_ids.product_qty = 4
        demand.action_cancel()
        self.assertEqual(demand.state, "cancel")
        demand.action_draft()
        demand.action_cancel()
        demand.unlink()
        self.assertFalse(demand.exists())

    def test_copy_keeps_lines_and_resets_state(self):
        demand = self._create_demand(
            self.user_shop_a, self.shop_a, [(self.product_1, 2)], send=True
        )
        copied = demand.copy()
        self.assertEqual(copied.state, "draft")
        self.assertNotEqual(copied.name, demand.name)
        self.assertEqual(copied.line_ids.product_id, self.product_1)

    def test_shop_user_only_sees_own_shop(self):
        demand_a = self._create_demand(self.user_shop_a, self.shop_a, [(self.product_1, 1)])
        demand_b = self._create_demand(self.user_shop_b, self.shop_b, [(self.product_1, 1)])
        demand_model = self.env["matriz.store.demand"]
        domain = [("id", "in", (demand_a | demand_b).ids)]
        self.assertEqual(demand_model.with_user(self.user_shop_a).search(domain), demand_a)
        self.assertEqual(
            demand_model.with_user(self.user_central).search(domain), demand_a | demand_b
        )
        with self.assertRaises(AccessError):
            demand_b.with_user(self.user_shop_a).read(["name"])
        with self.assertRaises(AccessError):
            self._create_demand(self.user_shop_a, self.shop_b, [(self.product_1, 1)])

    def test_shop_user_cannot_consolidate(self):
        with self.assertRaises(AccessError):
            self.env["matriz.store.demand.consolidate"].with_user(self.user_shop_a).create({
                "central_warehouse_id": self.central.id,
            })
