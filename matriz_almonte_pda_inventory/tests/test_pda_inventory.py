# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.matriz_almonte_pda_sale_import.controllers import (
    pda_pos_order_controller as pos_mod,
)
from odoo.addons.matriz_almonte_pda_sale_import.tests.test_pda_sale_import import (
    _FakeRequest,
)

from ..controllers import pda_inventory_controller as mod

PDA_PAYLOAD = {
    "uuid": "3f0c9c1e-7a41-4b8e-9d52-1c6e0a7b2f10",
    "fecha_hora": "09/11/2026 10:35:12",
    "lineas": [
        {"id_articulo": "REF001", "unidades": 12},
        {"id_articulo": "REF002", "unidades": 3.5},
    ],
}


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
        cls.pos = cls.env["pos.config"].create(
            {"name": "TPV Inventario", "picking_type_id": cls.shop.pos_type_id.id}
        )
        cls.seller = cls.env["res.users"].create(
            {
                "name": "Vendedora PDA",
                "login": "vendedora_pda_inv",
                "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.token = cls.env["matriz.almonte.api.token"].create(
            {
                "name": "PDA Inventario",
                "tienda_id": cls.pos.id,
                "sale_user_id": cls.seller.id,
            }
        )

        def product(name, code, **extra):
            vals = {"name": name, "default_code": code, "type": "consu",
                    "is_storable": True}
            vals.update(extra)
            return cls.env["product.product"].create(vals)

        cls.ref1 = product("Vela", "REF001")
        cls.ref2 = product("Medalla", "REF002")
        cls.other = product("Rosario no enviado", "R-01")
        cls.zero = product("Cero a la izquierda", "010001")
        cls.service = product("Grabado", "SRV-1", is_storable=False)
        cls.lot_item = product("Pulsera con lote", "LOT-1", tracking="lot")

        Quant = cls.env["stock.quant"]
        Quant._update_available_quantity(cls.ref1, cls.stock, 10)
        Quant._update_available_quantity(cls.other, cls.stock, 7)

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

    def _quant(self, product):
        return self.env["stock.quant"].search(
            [("product_id", "=", product.id), ("location_id", "=", self.stock.id)]
        )

    def _assert_pending(self, product, on_hand, counted):
        quant = self._quant(product)
        self.assertEqual(len(quant), 1)
        self.assertEqual(quant.quantity, on_hand, "el stock no se aplica todavía")
        self.assertTrue(quant.inventory_quantity_set)
        self.assertEqual(quant.inventory_quantity, counted)
        self.assertEqual(quant.inventory_diff_quantity, counted - on_hand)
        self.assertEqual(quant.user_id, self.seller)

    def test_pda_payload_leaves_counts_pending(self):
        data, status = self._call(PDA_PAYLOAD)
        self.assertEqual(status, 200, data)
        self.assertIs(data["success"], True)
        self.assertEqual(data["message"], "Inventario registrado")
        self.assertIsInstance(data["order_id"], int)
        self.assertEqual(data["order_name"], "INV/%05d" % data["order_id"])
        self.assertEqual(data["code"], "INVENTORY_COUNTED")
        self.assertEqual(data["almacen"], self.shop.name)
        self.assertEqual(data["articulos_contados"], 2)
        self.assertEqual(data["avisos"], [])
        self._assert_pending(self.ref1, on_hand=10, counted=12)
        self._assert_pending(self.ref2, on_hand=0, counted=3.5)
        # El artículo no enviado no se toca.
        other = self._quant(self.other)
        self.assertEqual(other.quantity, 7)
        self.assertFalse(other.inventory_quantity_set)
        # No se crea ningún movimiento hasta aplicar.
        self.assertFalse(self.env["stock.move"].search(
            [("is_inventory", "=", True), ("product_id", "in", [self.ref1.id, self.ref2.id])]
        ))

    def test_applying_from_odoo_sets_real_stock(self):
        self._call(PDA_PAYLOAD)
        quants = self._quant(self.ref1) | self._quant(self.ref2)
        quants.action_apply_inventory()
        self.assertEqual(self._quant(self.ref1).quantity, 12)
        self.assertEqual(self._quant(self.ref2).quantity, 3.5)
        self.assertFalse(quants.filtered("inventory_quantity_set"))

    def test_each_count_gets_its_own_number(self):
        first, _status = self._call(PDA_PAYLOAD)
        second, _status = self._call(PDA_PAYLOAD)
        self.assertEqual(second["order_id"], first["order_id"] + 1)
        self.assertNotEqual(second["order_name"], first["order_name"])
        # El recuento pendiente lleva el número del último envío.
        self.assertEqual(
            self._quant(self.ref1).pda_count_origin,
            "%s (PDA PDA Inventario)" % second["order_name"],
        )

    def test_pda_adjustments_are_distinguishable(self):
        data, _status = self._call(PDA_PAYLOAD)
        origin = "%s (PDA PDA Inventario)" % data["order_name"]
        quant1, quant2 = self._quant(self.ref1), self._quant(self.ref2)
        self.assertEqual(quant1.pda_count_origin, origin)
        self.assertEqual(
            self.env["stock.quant"].search(
                [("pda_count_origin", "!=", False), ("location_id", "=", self.stock.id)]
            ),
            quant1 | quant2,
        )
        # Aplicar desde el wizard «Aplicar todo» (que propone su propio
        # nombre) mantiene la referencia de la PDA.
        (quant1 | quant2).with_context(
            inventory_name="Cantidad de producto actualizada"
        )._apply_inventory()
        moves = self.env["stock.move"].search(
            [("is_inventory", "=", True), ("product_id", "in", [self.ref1.id, self.ref2.id])]
        )
        self.assertEqual(len(moves), 2)
        self.assertEqual(set(moves.mapped("reference")), {origin})
        self.assertFalse((quant1 | quant2).filtered("pda_count_origin"))

    def test_manual_edit_or_clear_removes_pda_mark(self):
        self._call(PDA_PAYLOAD)
        quant1, quant2 = self._quant(self.ref1), self._quant(self.ref2)
        quant1.inventory_quantity = 11
        self.assertFalse(quant1.pda_count_origin)
        quant1.action_apply_inventory()
        move = self.env["stock.move"].search(
            [("is_inventory", "=", True), ("product_id", "=", self.ref1.id)]
        )
        self.assertNotIn("PDA", move.reference)
        quant2.action_clear_inventory_quantity()
        self.assertFalse(quant2.pda_count_origin)
        self.assertFalse(quant2.inventory_quantity_set)

    def test_resend_and_repeated_lines(self):
        self._call(PDA_PAYLOAD)
        data, status = self._call(PDA_PAYLOAD)
        self.assertEqual(status, 200, data)
        self._assert_pending(self.ref1, on_hand=10, counted=12)
        data, status = self._call({"lineas": [
            {"id_articulo": "REF001", "unidades": 2},
            {"id_articulo": "REF001", "unidades": "4"},
        ]})
        self.assertEqual(status, 200, data)
        self._assert_pending(self.ref1, on_hand=10, counted=6)

    def test_ref_keeps_leading_zeros(self):
        data, status = self._call({"lineas": [{"id_articulo": "010001", "unidades": 1}]})
        self.assertEqual(status, 200, data)
        self._assert_pending(self.zero, on_hand=0, counted=1)
        data, status = self._call({"lineas": [{"id_articulo": 10001, "unidades": 1}]})
        self.assertEqual(data["articulos_contados"], 0)
        self.assertEqual(data["avisos"][0]["mensaje"], mod.NOT_FOUND)

    def test_unusable_refs_are_warnings(self):
        self.env["product.product"].create({"name": "Duplicada", "default_code": "R-01"})
        data, status = self._call({"lineas": [
            {"id_articulo": "REF001", "unidades": 1},
            {"id_articulo": "NOEXISTE", "unidades": 1},
            {"id_articulo": "R-01", "unidades": 1},
            {"id_articulo": "SRV-1", "unidades": 1},
            {"id_articulo": "LOT-1", "unidades": 1},
        ]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["articulos_contados"], 1)
        self.assertEqual(
            {(w["id_articulo"], w["mensaje"]) for w in data["avisos"]},
            {
                ("NOEXISTE", mod.NOT_FOUND),
                ("R-01", mod.AMBIGUOUS),
                ("SRV-1", mod.NOT_STORABLE),
                ("LOT-1", mod.TRACKED),
            },
        )
        self._assert_pending(self.ref1, on_hand=10, counted=1)
        self.assertFalse(self._quant(self.other).inventory_quantity_set)

    def test_rejects_invalid_lines(self):
        for lineas in (
            [],
            "x",
            [1],
            [{"unidades": 1}],
            [{"id_articulo": "", "unidades": 1}],
            [{"id_articulo": True, "unidades": 1}],
            [{"id_articulo": "REF001"}],
            [{"id_articulo": "REF001", "unidades": -1}],
            [{"id_articulo": "REF001", "unidades": "abc"}],
            [{"id_articulo": "REF001", "unidades": True}],
            [{"id_articulo": "REF001", "unidades": "nan"}],
        ):
            data, status = self._call({"lineas": lineas})
            self.assertEqual((status, data["code"]), (400, "INVALID_INVENTORY"), lineas)
        self.assertFalse(self._quant(self.ref1).inventory_quantity_set)

    def test_rejects_invalid_requests(self):
        data, status = self._call(b"")
        self.assertEqual((status, data["code"]), (400, "EMPTY_BODY"))
        data, status = self._call(b"{no json")
        self.assertEqual((status, data["code"]), (400, "INVALID_JSON"))
        data, status = self._call(b"[]")
        self.assertEqual((status, data["code"]), (400, "INVALID_JSON"))
        data, status = self._call(PDA_PAYLOAD, headers={})
        self.assertEqual((status, data["code"]), (401, "MISSING_TOKEN"))
        data, status = self._call(
            PDA_PAYLOAD, headers={"Authorization": "Bearer " + "x" * 43}
        )
        self.assertEqual((status, data["code"]), (401, "INVALID_TOKEN"))

    def test_token_without_warehouse(self):
        self.token.tienda_id = False
        data, status = self._call(PDA_PAYLOAD)
        self.assertEqual((status, data["code"]), (400, "TOKEN_WITHOUT_WAREHOUSE"))

    def test_unexpected_error_returns_server_error(self):
        model = type(self.env["stock.quant"])
        with patch.object(
            model, "_pda_set_counted_quantities", side_effect=RuntimeError("boom")
        ):
            data, status = self._call(PDA_PAYLOAD)
        self.assertEqual((status, data["code"]), (500, "SERVER_ERROR"))
