from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPurchaseDestination(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.central = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.shop = cls.env["stock.warehouse"].create({
            "name": "Tienda Olga", "code": "OLG", "company_id": cls.company.id,
        })
        cls.vendor = cls.env["res.partner"].create({"name": "Proveedor velas"})
        cls.candle = cls.env["product.product"].create({
            "name": "Vela", "type": "consu", "is_storable": True,
        })
        cls.medal = cls.env["product.product"].create({
            "name": "Medalla", "type": "consu", "is_storable": True,
        })

    def _create_order(self, dest_warehouse=None, lines=None):
        lines = lines or [(self.candle, 10), (self.medal, 4)]
        return self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "picking_type_id": self.central.in_type_id.id,
            "dest_warehouse_id": dest_warehouse.id if dest_warehouse else False,
            "order_line": [
                Command.create({"product_id": product.id, "product_qty": qty, "price_unit": 1})
                for product, qty in lines
            ],
        })

    def _receive(self, receipt, quantities=None, backorder=True):
        for move in receipt.move_ids:
            move.quantity = (quantities or {}).get(move.product_id, move.product_uom_qty)
        receipt.move_ids.picked = True
        receipt.with_context(cancel_backorder=not backorder)._action_done()

    def test_full_receipt_creates_transfer_to_destination(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        receipt = order.picking_ids
        self.assertEqual(receipt.picking_type_id, self.central.in_type_id)
        self.assertFalse(order.destination_picking_ids)
        self._receive(receipt)
        transfer = order.destination_picking_ids
        self.assertEqual(len(transfer), 1)
        self.assertEqual(order.destination_picking_count, 1)
        self.assertEqual(transfer.picking_type_id, self.central.int_type_id)
        self.assertEqual(transfer.location_id, receipt.location_dest_id)
        self.assertEqual(transfer.location_dest_id, self.shop.lot_stock_id)
        self.assertEqual(transfer.destination_receipt_id, receipt)
        self.assertEqual(transfer.state, "assigned")
        self.assertIn(order.name, transfer.origin)
        self.assertEqual(
            {move.product_id: move.product_uom_qty for move in transfer.move_ids},
            {self.candle: 10, self.medal: 4},
        )
        transfer.button_validate()
        self.assertEqual(
            self.candle.with_context(warehouse_id=self.shop.id).qty_available, 10
        )

    def test_partial_receipts_create_one_transfer_each(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        self._receive(order.picking_ids, {self.candle: 6, self.medal: 0})
        first = order.destination_picking_ids
        self.assertEqual(
            {move.product_id: move.product_uom_qty for move in first.move_ids},
            {self.candle: 6},
        )
        backorder = order.picking_ids.filtered(lambda picking: picking.state != "done")
        self._receive(backorder)
        second = order.destination_picking_ids - first
        self.assertEqual(
            {move.product_id: move.product_uom_qty for move in second.move_ids},
            {self.candle: 4, self.medal: 4},
        )

    def test_receipt_with_nothing_received_creates_no_transfer(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        receipt = order.picking_ids
        receipt.move_ids.quantity = 0
        receipt._create_destination_transfers()
        self.assertFalse(order.destination_picking_ids)

    def test_no_destination_or_same_warehouse_creates_nothing(self):
        for dest in (None, self.central):
            order = self._create_order(dest)
            order.button_confirm()
            self._receive(order.picking_ids)
            self.assertFalse(order.destination_picking_ids)

    def test_transfer_is_not_duplicated(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        receipt = order.picking_ids
        self._receive(receipt)
        self.assertFalse(receipt._create_destination_transfers())
        self.assertEqual(len(order.destination_picking_ids), 1)

    def test_cannot_remove_destination_after_transfer(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        self._receive(order.picking_ids)
        with self.assertRaises(ValidationError):
            order.dest_warehouse_id = False

    def test_destination_editable_in_form(self):
        with Form(self.env["purchase.order"]) as order_form:
            order_form.partner_id = self.vendor
            order_form.dest_warehouse_id = self.shop
            with order_form.order_line.new() as line:
                line.product_id = self.candle
                line.product_qty = 2
        self.assertEqual(order_form.record.dest_warehouse_id, self.shop)

    def test_view_destination_pickings_action(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        self._receive(order.picking_ids, {self.candle: 5, self.medal: 4})
        action = order.action_view_destination_pickings()
        self.assertEqual(action["res_id"], order.destination_picking_ids.id)
        self._receive(order.picking_ids.filtered(lambda p: p.state != "done"))
        action = order.action_view_destination_pickings()
        self.assertEqual(sorted(action["domain"][0][2]), sorted(order.destination_picking_ids.ids))
        self.assertFalse(action.get("res_id"))
        self.assertEqual(len(order.destination_picking_ids), 2)

    def test_destination_message_posted(self):
        order = self._create_order(self.shop)
        order.button_confirm()
        self._receive(order.picking_ids)
        self.assertTrue(any(
            "Tienda Olga" in (message.body or "") for message in order.message_ids
        ))
