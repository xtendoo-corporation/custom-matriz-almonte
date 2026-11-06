# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import json
from unittest.mock import patch
from uuid import uuid4

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.matriz_almonte_pda_sale_import.controllers import (
    pda_pos_order_controller as pos_mod,
)
from odoo.addons.matriz_almonte_pda_sale_import.tests.test_pda_sale_import import (
    _FakeRequest,
)

from ..controllers import pda_inventory_controller as mod
from ..models.matriz_almonte_pda_inventory import SKIP_NOT_STORABLE, SKIP_TRACKED


@tagged("post_install", "-at_install")
class TestPdaInventory(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        company = cls.env.company
        cls.shop = cls.env["stock.warehouse"].create(
            {"name": "Tienda Inventario", "code": "TIN", "company_id": company.id}
        )
        cls.stock = cls.shop.lot_stock_id
        cls.shelf = cls.env["stock.location"].create(
            {"name": "Estantería", "location_id": cls.stock.id, "usage": "internal"}
        )
        cls.other_shop = cls.env["stock.warehouse"].create(
            {"name": "Otra tienda", "code": "OTI", "company_id": company.id}
        )
        cls.pos = cls.env["pos.config"].create(
            {"name": "TPV Inventario", "picking_type_id": cls.shop.pos_type_id.id}
        )
        cls.token = cls.env["matriz.almonte.api.token"].create(
            {
                "name": "PDA Inventario",
                "tienda_id": cls.pos.id,
                "sale_user_id": cls.env.user.id,
            }
        )

        def product(name, code, **extra):
            vals = {"name": name, "default_code": code, "type": "consu",
                    "is_storable": True}
            vals.update(extra)
            return cls.env["product.product"].create(vals)

        cls.candle = product("Vela", "010001")
        cls.medal = product("Medalla", "10001")
        cls.forgotten = product("Rosario no contado", "R-01")
        cls.new_item = product("Llavero sin stock", "L-01")
        cls.service = product("Grabado", "SRV-1", is_storable=False)
        cls.lot_item = product("Pulsera con lote", "LOT-1", tracking="lot")

        Quant = cls.env["stock.quant"]
        Quant._update_available_quantity(cls.candle, cls.stock, 10)
        Quant._update_available_quantity(cls.candle, cls.shelf, 4)
        Quant._update_available_quantity(cls.medal, cls.stock, 5)
        Quant._update_available_quantity(cls.forgotten, cls.shelf, 7)
        Quant._update_available_quantity(cls.forgotten, cls.other_shop.lot_stock_id, 3)
        cls.lot = cls.env["stock.lot"].create(
            {"name": "L1", "product_id": cls.lot_item.id}
        )
        Quant._update_available_quantity(cls.lot_item, cls.stock, 2, lot_id=cls.lot)

    def _call(self, payload, headers=None):
        if headers is None:
            headers = {"Authorization": f"Bearer {self.token.token}"}
        fake = _FakeRequest(
            self.env,
            headers=headers,
            body=payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
        )
        with patch.object(mod, "request", fake), patch.object(pos_mod, "request", fake):
            response = mod.MatrizAlmontePdaInventoryController().pda_inventory()
        return json.loads(response.get_data(as_text=True)), response.status_code

    def _payload(self, lineas=None, **extra):
        payload = {
            "uuid": str(uuid4()),
            "lineas": lineas if lineas is not None else [
                {"ref": "010001", "unidades": 6},
                {"ref": "010001", "unidades": 2},
                {"ref": 10001, "unidades": 5},
                {"ref": "L-01", "unidades": 3},
            ],
        }
        payload.update(extra)
        return payload

    def _qty(self, product, location=None):
        return product.with_context(
            location=(location or self.stock).id
        ).qty_available

    def _inventory(self, data):
        return self.env["matriz.almonte.pda.inventory"].browse(data["inventory_id"])

    def test_full_inventory_is_applied(self):
        data, status = self._call(self._payload(nota="Recuento de noviembre"))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["code"], "INVENTORY_APPLIED")
        self.assertEqual(data["almacen"], self.shop.name)
        self.assertEqual(data["avisos"], [])
        # Contados: la suma de líneas repetidas se queda en la ubicación de
        # existencias y las sububicaciones del artículo quedan a 0.
        self.assertEqual(self._qty(self.candle), 8)
        self.assertEqual(self._qty(self.candle, self.shelf), 0)
        self.assertEqual(self._qty(self.medal), 5)
        self.assertEqual(self._qty(self.new_item), 3)
        # No contado: a 0 en este almacén, sin tocar otros almacenes.
        self.assertEqual(self._qty(self.forgotten), 0)
        self.assertEqual(self._qty(self.forgotten, self.other_shop.lot_stock_id), 3)
        # No contado con lote: también a 0.
        self.assertEqual(self._qty(self.lot_item), 0)

        inventory = self._inventory(data)
        self.assertEqual(inventory.warehouse_id, self.shop)
        self.assertEqual(inventory.token_id, self.token)
        self.assertEqual(inventory.user_id, self.env.user)
        self.assertEqual(inventory.note, "Recuento de noviembre")
        self.assertEqual(inventory.counted_count, 3)
        self.assertEqual(inventory.adjusted_count, 4)
        self.assertEqual(inventory.zeroed_count, 2)
        self.assertEqual(data["articulos_contados"], 3)
        self.assertEqual(data["articulos_a_cero"], 2)
        lines = {line.product_id: line for line in inventory.line_ids}
        self.assertEqual(lines[self.candle].theoretical_qty, 14)
        self.assertEqual(lines[self.candle].counted_qty, 8)
        self.assertEqual(lines[self.candle].difference_qty, -6)
        self.assertEqual(lines[self.medal].state, "no_change")
        self.assertEqual(lines[self.medal].pda_ref, "10001")
        self.assertFalse(lines[self.forgotten].counted)
        self.assertEqual(lines[self.forgotten].difference_qty, -7)

        moves = inventory._inventory_moves()
        self.assertEqual(inventory.move_count, len(moves))
        self.assertTrue(moves)
        self.assertTrue(all(move.state == "done" for move in moves))
        self.assertEqual(set(moves.mapped("reference")), {inventory.name})

    def test_resend_is_idempotent(self):
        payload = self._payload()
        first, _status = self._call(payload)
        self.env["stock.quant"]._update_available_quantity(self.candle, self.stock, 1)
        data, status = self._call(payload)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["code"], "DUPLICATE")
        self.assertEqual(data["inventory_id"], first["inventory_id"])
        self.assertEqual(self._qty(self.candle), 9)

    def test_tracked_and_not_storable_products_are_warnings(self):
        data, status = self._call(self._payload(lineas=[
            {"ref": "LOT-1", "unidades": 9},
            {"ref": "SRV-1", "unidades": 1},
            {"ref": "010001", "unidades": 14},
        ]))
        self.assertEqual(status, 200, data)
        self.assertEqual(
            {(warning["ref"], warning["mensaje"]) for warning in data["avisos"]},
            {("LOT-1", SKIP_TRACKED), ("SRV-1", SKIP_NOT_STORABLE)},
        )
        # El artículo con lote contado no se toca.
        self.assertEqual(self._qty(self.lot_item), 2)
        self.assertEqual(self._inventory(data).skipped_count, 2)

    def test_unknown_or_ambiguous_ref_rejects_everything(self):
        data, status = self._call(self._payload(lineas=[
            {"ref": "010001", "unidades": 1},
            {"ref": "0010001", "unidades": 1},
        ]))
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "INVALID_INVENTORY")
        self.assertIn("0010001", data["message"])
        self.assertEqual(self._qty(self.candle), 14)
        self.assertEqual(self._qty(self.forgotten, self.shelf), 7)

        self.env["product.product"].create({"name": "Duplicada", "default_code": "R-01"})
        data, status = self._call(self._payload(lineas=[{"ref": "R-01", "unidades": 1}]))
        self.assertEqual(status, 400)
        self.assertIn("varios artículos", data["message"])
        self.assertFalse(self.env["matriz.almonte.pda.inventory"].search(
            [("warehouse_id", "=", self.shop.id)]
        ))

    def test_id_articulo_is_accepted_as_ref(self):
        data, status = self._call(self._payload(lineas=[
            {"id_articulo": "10001", "unidades": "2.5"},
        ]))
        self.assertEqual(status, 200, data)
        self.assertEqual(self._qty(self.medal), 2.5)

    def test_rejects_invalid_lines(self):
        for lineas in (
            [],
            "x",
            [1],
            [{"unidades": 1}],
            [{"ref": "", "unidades": 1}],
            [{"ref": True, "unidades": 1}],
            [{"ref": "010001"}],
            [{"ref": "010001", "unidades": -1}],
            [{"ref": "010001", "unidades": "abc"}],
            [{"ref": "010001", "unidades": True}],
            [{"ref": "010001", "unidades": "nan"}],
        ):
            data, status = self._call(self._payload(lineas=lineas))
            self.assertEqual((status, data["code"]), (400, "INVALID_INVENTORY"), lineas)
        self.assertEqual(self._qty(self.candle), 14)

    def test_rejects_invalid_requests(self):
        data, status = self._call(self._payload(uuid=""))
        self.assertEqual((status, data["code"]), (400, "MISSING_UUID"))
        data, status = self._call(b"")
        self.assertEqual((status, data["code"]), (400, "EMPTY_BODY"))
        data, status = self._call(b"{no json")
        self.assertEqual((status, data["code"]), (400, "INVALID_JSON"))
        data, status = self._call(b"[]")
        self.assertEqual((status, data["code"]), (400, "INVALID_JSON"))
        data, status = self._call(self._payload(), headers={})
        self.assertEqual((status, data["code"]), (401, "MISSING_TOKEN"))
        data, status = self._call(
            self._payload(), headers={"Authorization": "Bearer " + "x" * 43}
        )
        self.assertEqual((status, data["code"]), (401, "INVALID_TOKEN"))

    def test_token_without_warehouse(self):
        self.token.tienda_id = False
        data, status = self._call(self._payload())
        self.assertEqual((status, data["code"]), (400, "TOKEN_WITHOUT_WAREHOUSE"))

    def test_unexpected_error_returns_server_error(self):
        model = type(self.env["matriz.almonte.pda.inventory"])
        with patch.object(model, "_apply_pda_count", side_effect=RuntimeError("boom")):
            data, status = self._call(self._payload())
        self.assertEqual((status, data["code"]), (500, "SERVER_ERROR"))
