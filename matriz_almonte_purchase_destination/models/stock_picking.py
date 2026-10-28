from odoo import _, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    purchase_destination_id = fields.Many2one(
        "purchase.order",
        string="Compra origen (destino)",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="Pedido de compra cuyo almacén destino originó este traspaso.",
    )
    destination_receipt_id = fields.Many2one(
        "stock.picking",
        string="Recepción origen",
        readonly=True,
        copy=False,
        index="btree_not_null",
    )

    def _action_done(self):
        result = super()._action_done()
        self._create_destination_transfers()
        return result

    def _create_destination_transfers(self):
        receipts = self.filtered(
            lambda picking: picking.state == "done"
            and picking.picking_type_code == "incoming"
            and picking.purchase_id
            and picking.purchase_id._needs_destination_transfer()
        )
        if not receipts:
            return self.browse()
        already_done = self.search([("destination_receipt_id", "in", receipts.ids)])
        transfers = self.browse()
        for receipt in receipts - already_done.destination_receipt_id:
            moves = receipt.move_ids.filtered(
                lambda move: move.state == "done" and move.quantity > 0
            )
            if moves:
                transfers |= receipt._create_destination_transfer(moves)
        return transfers

    def _create_destination_transfer(self, moves):
        self.ensure_one()
        order = self.purchase_id
        source_warehouse = self.picking_type_id.warehouse_id
        destination = order.dest_warehouse_id
        transfer = self.create({
            "picking_type_id": source_warehouse.int_type_id.id,
            "location_id": self.location_dest_id.id,
            "location_dest_id": destination.lot_stock_id.id,
            "partner_id": destination.partner_id.id,
            "origin": f"{order.name} ({self.name})",
            "company_id": self.company_id.id,
            "purchase_destination_id": order.id,
            "destination_receipt_id": self.id,
            "move_ids": [
                fields.Command.create({
                    "product_id": move.product_id.id,
                    "product_uom": move.product_uom.id,
                    "product_uom_qty": move.quantity,
                    "location_id": self.location_dest_id.id,
                    "location_dest_id": destination.lot_stock_id.id,
                    "origin": order.name,
                    "company_id": self.company_id.id,
                })
                for move in moves
            ],
        })
        transfer.action_confirm()
        transfer.action_assign()
        order.message_post(body=_(
            "Recepción %(receipt)s validada: creado el traspaso %(transfer)s a %(warehouse)s.",
            receipt=self._get_html_link(),
            transfer=transfer._get_html_link(),
            warehouse=destination.name,
        ))
        return transfer
