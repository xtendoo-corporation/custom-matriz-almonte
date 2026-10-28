from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import StoreDemandCommon


@tagged("post_install", "-at_install")
class TestStoreDemandConsolidate(StoreDemandCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.demand_a = cls._create_demand(
            cls, cls.user_shop_a, cls.shop_a,
            [(cls.product_1, 4), (cls.product_3, 2)], send=True,
        )
        cls.demand_b = cls._create_demand(
            cls, cls.user_shop_b, cls.shop_b,
            [(cls.product_1, 6), (cls.product_2, 1)], send=True,
        )
        cls.demand_a = cls.demand_a.with_env(cls.env)
        cls.demand_b = cls.demand_b.with_env(cls.env)
        cls.demands = cls.demand_a | cls.demand_b

    def _wizard_line(self, wizard, product):
        return wizard.line_ids.filtered(lambda line: line.product_id == product)

    def test_wizard_groups_demand_by_product_and_vendor(self):
        draft = self._create_demand(self.user_shop_a, self.shop_a, [(self.product_2, 9)])
        self._set_central_stock(self.product_1, 3)
        wizard = self._open_wizard()
        self.assertEqual(wizard.central_warehouse_id, self.central)
        self.assertEqual(len(wizard.line_ids), 3)
        self.assertEqual(wizard.demand_count, 2)
        self.assertEqual(wizard.purchase_partner_count, 2)
        line_1 = self._wizard_line(wizard, self.product_1)
        self.assertEqual(line_1.demand_qty, 10)
        self.assertEqual(line_1.central_free_qty, 3)
        self.assertEqual(line_1.purchase_qty, 7)
        self.assertEqual(line_1.partner_id, self.vendor_1)
        self.assertIn("TDA: 4", line_1.shop_names)
        self.assertIn("TDB: 6", line_1.shop_names)
        self.assertEqual(self._wizard_line(wizard, self.product_2).demand_qty, 1)
        self.assertNotIn(draft.line_ids, wizard.line_ids.demand_line_ids)

    def test_toggle_central_stock_recomputes_purchase_qty(self):
        self._set_central_stock(self.product_1, 3)
        wizard = self._open_wizard()
        with Form(wizard) as wizard_form:
            wizard_form.use_central_stock = False
        self.assertEqual(self._wizard_line(wizard, self.product_1).purchase_qty, 10)
        with Form(wizard) as wizard_form:
            wizard_form.use_central_stock = True
        self.assertEqual(self._wizard_line(wizard, self.product_1).purchase_qty, 7)

    def test_consolidate_creates_one_purchase_per_vendor_and_one_transfer_per_shop(self):
        self._set_central_stock(self.product_1, 3)
        wizard = self._open_wizard()
        action = wizard.action_consolidate()
        purchases = self.env["purchase.order"].search(action["domain"])
        self.assertEqual(purchases.partner_id, self.vendor_1 | self.vendor_2)
        purchase_1 = purchases.filtered(lambda po: po.partner_id == self.vendor_1)
        self.assertEqual(purchase_1.picking_type_id, self.central.in_type_id)
        self.assertIn(self.demand_a.name, purchase_1.origin)
        self.assertIn(self.demand_b.name, purchase_1.origin)
        qty_by_product = {
            line.product_id: line.product_qty for line in purchase_1.order_line
        }
        self.assertEqual(qty_by_product, {self.product_1: 7, self.product_2: 1})
        self.assertEqual(purchase_1.order_line[0].price_unit, 2.0)

        self.assertEqual(self.demands.mapped("state"), ["consolidated", "consolidated"])
        self.assertTrue(all(self.demands.line_ids.mapped("is_consolidated")))
        picking_a = self.demand_a.picking_ids
        self.assertEqual(len(picking_a), 1)
        self.assertEqual(picking_a.picking_type_id, self.central.int_type_id)
        self.assertEqual(picking_a.location_id, self.central.lot_stock_id)
        self.assertEqual(picking_a.location_dest_id, self.shop_a.lot_stock_id)
        self.assertEqual(
            sorted(picking_a.move_ids.mapped("product_uom_qty")), [2.0, 4.0]
        )
        self.assertEqual(self.demand_a.purchase_ids, purchases)
        self.assertEqual(self.demand_b.purchase_ids, purchase_1)
        self.assertEqual(purchase_1.store_demand_ids, self.demands)
        self.assertEqual(purchase_1.store_demand_count, 2)
        domain = purchase_1.action_view_store_demands()["domain"]
        self.assertEqual(sorted(domain[0][2]), sorted(self.demands.ids))
        domain = self.demand_a.action_view_purchases()["domain"]
        self.assertEqual(sorted(domain[0][2]), sorted(purchases.ids))
        self.assertEqual(
            self.demand_a.action_view_pickings()["domain"],
            [("id", "in", picking_a.ids)],
        )

        with self.assertRaisesRegex(UserError, "ya consolidadas"):
            self.demand_a.line_ids.with_user(self.user_central).product_qty = 1
        with self.assertRaisesRegex(UserError, "ya se ha consolidado"):
            self.demand_a.action_draft()
        with self.assertRaisesRegex(UserError, "ya no están pendientes"):
            wizard.action_consolidate()

    def test_full_central_stock_skips_purchase(self):
        for product in (self.product_1, self.product_2, self.product_3):
            self._set_central_stock(product, 50)
        wizard = self._open_wizard()
        action = wizard.action_consolidate()
        self.assertEqual(action["res_model"], "stock.picking")
        self.assertFalse(self.demands.purchase_ids)
        pickings = self.demands.picking_ids
        self.assertEqual(pickings.mapped("state"), ["assigned", "assigned"])

    def test_receipt_reserves_transfers_and_validation_marks_done(self):
        wizard = self._open_wizard()
        wizard.action_consolidate()
        purchases = self.demands.purchase_ids
        purchases.button_confirm()
        picking_a = self.demand_a.picking_ids
        self.assertNotEqual(picking_a.state, "assigned")
        purchases.picking_ids.button_validate()
        self.assertEqual(picking_a.state, "assigned")
        picking_a.button_validate()
        self.assertEqual(self.demand_a.state, "done")
        self.assertEqual(self.demand_b.state, "consolidated")

    def test_partial_transfer_with_backorder(self):
        for product in (self.product_1, self.product_3):
            self._set_central_stock(product, 50)
        wizard = self._open_wizard(self.demand_a.line_ids)
        wizard.action_consolidate()
        self.assertEqual(self.demand_b.state, "sent")
        picking = self.demand_a.picking_ids
        move_1 = picking.move_ids.filtered(lambda move: move.product_id == self.product_1)
        move_1.quantity = 1
        picking.move_ids.filtered(lambda move: move.product_id == self.product_3).quantity = 2
        picking.move_ids.picked = True
        picking.with_context(cancel_backorder=False)._action_done()
        self.assertEqual(self.demand_a.state, "consolidated")
        backorder = picking.backorder_ids
        self.assertTrue(backorder)
        backorder.move_ids.picked = True
        backorder._action_done()
        self.assertEqual(self.demand_a.state, "done")

    def test_missing_vendor_blocks_consolidation(self):
        demand = self._create_demand(
            self.user_shop_a, self.shop_a, [(self.product_no_vendor, 2)], send=True
        )
        wizard = self._open_wizard(demand.line_ids)
        with self.assertRaisesRegex(UserError, "Rosario"):
            wizard.action_consolidate()
        wizard.line_ids.partner_id = self.vendor_2
        wizard.action_consolidate()
        demand = demand.with_env(self.env)
        self.assertEqual(demand.line_ids.partner_id, self.vendor_2)
        self.assertEqual(demand.purchase_ids.partner_id, self.vendor_2)

    def test_open_from_selected_demand_lines(self):
        action = self.demand_b.line_ids.with_user(self.user_central).action_open_consolidate()
        wizard = self.env["matriz.store.demand.consolidate"].browse(action["res_id"])
        self.assertEqual(wizard.line_ids.demand_line_ids, self.demand_b.line_ids)
        server_action = self.env.ref(
            "matriz_almonte_store_demand.action_server_store_demand_consolidate"
        ).with_user(self.user_central).run()
        self.assertEqual(server_action["res_model"], "matriz.store.demand.consolidate")

    def test_open_without_pending_lines_raises(self):
        draft = self._create_demand(self.user_shop_a, self.shop_a, [(self.product_1, 1)])
        with self.assertRaisesRegex(UserError, "No hay líneas"):
            self._open_wizard(draft.line_ids)

    def test_lines_from_several_centrals_raise(self):
        new_central = self.env["stock.warehouse"].create({
            "name": "Central 2", "code": "CT2", "company_id": self.company.id,
        })
        self.demand_b.central_warehouse_id = new_central
        with self.assertRaisesRegex(UserError, "varios almacenes centrales"):
            self._open_wizard()

    def test_partial_consolidation_keeps_demand_sent(self):
        first_line = self.demand_a.line_ids[0]
        wizard = self._open_wizard(first_line)
        wizard.action_consolidate()
        self.assertTrue(first_line.is_consolidated)
        self.assertFalse(self.demand_a.line_ids[1].is_consolidated)
        self.assertEqual(self.demand_a.state, "sent")
        wizard = self._open_wizard(self.demand_a.line_ids)
        self.assertEqual(wizard.line_ids.demand_line_ids, self.demand_a.line_ids[1])
        wizard.action_consolidate()
        self.assertEqual(self.demand_a.state, "consolidated")
