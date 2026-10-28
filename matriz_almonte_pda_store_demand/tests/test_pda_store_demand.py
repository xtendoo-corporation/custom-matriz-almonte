# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import json
from unittest.mock import patch
from uuid import uuid4

from odoo import Command
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.matriz_almonte_pda_sale_import.controllers import (
    pda_pos_order_controller as pos_mod,
)
from odoo.addons.matriz_almonte_pda_sale_import.tests.test_pda_sale_import import (
    _FakeRequest,
)

from ..controllers import pda_store_demand_controller as mod


@tagged("post_install", "-at_install")
class TestPdaStoreDemand(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        company = cls.env.company
        cls.central = cls.env["stock.warehouse"].search(
            [("company_id", "=", company.id)], limit=1
        )
        cls.shop = cls.env["stock.warehouse"].create(
            {"name": "Tienda PDA", "code": "TPD", "company_id": company.id}
        )
        cls.pos = cls.env["pos.config"].create(
            {"name": "TPV Tienda PDA", "picking_type_id": cls.shop.pos_type_id.id}
        )
        cls.token = cls.env["matriz.almonte.api.token"].create(
            {
                "name": "Token pre-pedidos",
                "tienda_id": cls.pos.id,
                "sale_user_id": cls.env.user.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {"name": "Vela PDA", "type": "consu", "purchase_ok": True}
        )
        cls.unlinked = cls.env["product.product"].create(
            {"name": "Medalla sin proveedor", "type": "consu", "purchase_ok": True}
        )
        cls.no_purchase = cls.env["product.product"].create(
            {"name": "No comprable", "type": "consu", "purchase_ok": False}
        )
        cls.supplier = cls.env["res.partner"].create(
            {"name": "Cerería PDA", "supplier_rank": 1, "ref": "P01", "vat": "ESB00000000"}
        )
        cls.other_supplier = cls.env["res.partner"].create({"name": "Proveedor sin rango"})
        cls.customer = cls.env["res.partner"].create({"name": "Cliente PDA"})
        cls.env["product.supplierinfo"].create({
            "partner_id": cls.supplier.id,
            "product_tmpl_id": cls.product.product_tmpl_id.id,
            "price": 4.5,
        })
        cls.env["product.supplierinfo"].create({
            "partner_id": cls.other_supplier.id,
            "product_tmpl_id": cls.unlinked.product_tmpl_id.id,
            "price": 1.0,
        })

    def _call(self, payload, auth=True):
        fake = _FakeRequest(
            self.env,
            headers={"Authorization": f"Bearer {self.token.token}"} if auth else {},
            body=payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
        )
        controller = mod.MatrizAlmontePdaStoreDemandController()
        method = controller.pda_list_suppliers if payload is None else (
            controller.pda_create_store_demand
        )
        with patch.object(mod, "request", fake), patch.object(pos_mod, "request", fake):
            response = method()
        return json.loads(response.get_data(as_text=True)), response.status_code

    def _payload(self, **extra):
        payload = {
            "uuid": str(uuid4()),
            "id_proveedor": self.supplier.id,
            "nota": "urgente <b>",
            "lineas": [
                {"id_articulo": self.product.id, "unidades": 3, "stock_tienda": 1},
                {"id_articulo": self.product.id, "unidades": 2},
            ],
        }
        payload.update(extra)
        return payload

    def _order(self, data):
        return self.env["purchase.order"].browse(data["order_id"])

    def test_creates_draft_purchase_grouping_lines(self):
        payload = self._payload()
        data, status = self._call(payload)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["success"])
        self.assertEqual(data["code"], "PURCHASE_CREATED")
        self.assertEqual(data["avisos"], [])
        order = self._order(data)
        self.assertEqual(data["order_name"], order.name)
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.partner_id, self.supplier)
        self.assertEqual(order.dest_warehouse_id, self.shop)
        self.assertNotEqual(order.picking_type_id.warehouse_id, self.shop)
        self.assertTrue(order._needs_destination_transfer())
        self.assertEqual(order.pda_uuid, payload["uuid"])
        self.assertIn("urgente", str(order.note))
        self.assertIn("TPV Tienda PDA", order.origin)
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_qty, 5)
        self.assertEqual(order.order_line.price_unit, 4.5)
        message = order.message_ids.filtered(lambda msg: "Token pre-pedidos" in str(msg.body))
        self.assertTrue(message)
        self.assertIn("urgente &lt;b&gt;", str(message.body))

    def test_products_without_supplier_return_warnings(self):
        data, status = self._call(self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 1},
            {"id_articulo": self.unlinked.id, "unidades": "2"},
        ], nota=""))
        self.assertEqual(status, 200, data)
        self.assertEqual(
            [
                warning["id_articulo"] for warning in data["avisos"]
                if warning["mensaje"] == mod._NO_SELLER_WARNING
            ],
            [self.unlinked.id],
        )
        order = self._order(data)
        self.assertFalse(order.note)
        line = order.order_line.filtered(lambda ln: ln.product_id == self.unlinked)
        self.assertEqual(line.price_unit, 0)
        self.assertTrue(order.message_ids.filtered(
            lambda msg: "Medalla sin proveedor" in str(msg.body)
        ))

    def test_resend_is_idempotent(self):
        payload = self._payload()
        first, _status = self._call(payload)
        second, status = self._call(payload)
        self.assertEqual(status, 200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["code"], "DUPLICATE")
        self.assertEqual(second["order_id"], first["order_id"])
        self.assertEqual(
            self.env["purchase.order"].search_count([("pda_uuid", "=", payload["uuid"])]),
            1,
        )

    def test_rejects_invalid_demand(self):
        for payload in (
            self._payload(lineas=[]),
            self._payload(lineas="x"),
            self._payload(lineas=[{"id_articulo": i, "unidades": 1} for i in range(501)]),
            self._payload(lineas=[{"id_articulo": self.product.id, "unidades": 0}]),
            self._payload(lineas=[{"id_articulo": "abc", "unidades": 1}]),
            self._payload(lineas=["no-dict"]),
            self._payload(lineas=[{"id_articulo": 99999999, "unidades": 1}]),
            self._payload(lineas=[{"id_articulo": self.no_purchase.id, "unidades": 1}]),
            self._payload(lineas=[{"id_articulo": self.product.id, "unidades": float("nan")}]),
        ):
            data, status = self._call(payload)
            self.assertEqual(status, 400, data)
            self.assertEqual(data["code"], "INVALID_DEMAND")
        self.assertFalse(self.env["purchase.order"].search(
            [("dest_warehouse_id", "=", self.shop.id)]
        ))

    def test_rejects_invalid_requests(self):
        cases = (
            (self._payload(uuid=""), "MISSING_UUID", 400),
            (self._payload(id_proveedor=None), "MISSING_SUPPLIER", 400),
            (self._payload(id_proveedor="abc"), "INVALID_SUPPLIER", 400),
            (self._payload(id_proveedor=self.customer.id), "INVALID_SUPPLIER", 400),
            (self._payload(id_proveedor=99999999), "INVALID_SUPPLIER", 400),
            (b"", "EMPTY_BODY", 400),
            (b"{no json", "INVALID_JSON", 400),
            (b"[1, 2]", "INVALID_JSON", 400),
            (b" " * (mod._MAX_PAYLOAD_BYTES + 1), "PAYLOAD_TOO_LARGE", 413),
        )
        for payload, code, expected_status in cases:
            data, status = self._call(payload)
            self.assertEqual((status, data["code"]), (expected_status, code), data)
            self.assertFalse(data["success"])

    def test_supplier_through_supplierinfo_is_accepted(self):
        data, status = self._call(self._payload(id_proveedor=self.other_supplier.id))
        self.assertEqual(status, 200, data)
        self.assertEqual(self._order(data).partner_id, self.other_supplier)

    def test_token_without_warehouse(self):
        self.pos.picking_type_id.warehouse_id = False
        data, status = self._call(self._payload())
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "TOKEN_WITHOUT_WAREHOUSE")

    def test_unexpected_error_returns_server_error(self):
        with patch.object(
            type(self.env["purchase.order"]), "create", side_effect=RuntimeError("boom")
        ), self.assertLogs(mod.__name__, level="ERROR"):
            data, status = self._call(self._payload())
        self.assertEqual(status, 500)
        self.assertEqual(data["code"], "SERVER_ERROR")

    def test_list_suppliers(self):
        data, status = self._call(None)
        self.assertEqual(status, 200, data)
        by_id = {supplier["id"]: supplier for supplier in data["proveedores"]}
        self.assertEqual(data["count"], len(data["proveedores"]))
        self.assertEqual(
            by_id[self.supplier.id],
            {"id": self.supplier.id, "nombre": "Cerería PDA", "ref": "P01",
             "nif": "ESB00000000"},
        )
        self.assertEqual(by_id[self.other_supplier.id]["ref"], "")
        self.assertNotIn(self.customer.id, by_id)

    def test_requires_token(self):
        for payload in (self._payload(), None):
            data, status = self._call(payload, auth=False)
            self.assertEqual(status, 401)
            self.assertEqual(data["code"], "MISSING_TOKEN")

    def _line(self, data, product=None):
        return self._order(data).order_line.filtered(
            lambda line: line.product_id == (product or self.product)
        )

    def _stock_warning_ids(self, data):
        return [
            warning["id_articulo"] for warning in data["avisos"]
            if warning["mensaje"] == mod._NO_SHOP_STOCK_WARNING
        ]

    def test_shop_stock_is_stored(self):
        data, status = self._call(self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 2.5, "stock_tienda": 3},
        ]))
        self.assertEqual(status, 200, data)
        line = self._line(data)
        self.assertEqual(line.shop_stock_qty, 3)
        self.assertTrue(line.shop_stock_reported)
        self.assertEqual(line.product_qty, 2.5)
        self.assertEqual(line.price_unit, 4.5)
        self.assertEqual(self._stock_warning_ids(data), [])
        self.assertTrue(self._order(data).message_ids.filtered(
            lambda msg: "Vela PDA: pide 2.5, en tienda 3" in str(msg.body)
        ))

    def test_shop_stock_zero_is_reported(self):
        data, status = self._call(self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 1, "stock_tienda": 0},
        ]))
        self.assertEqual(status, 200, data)
        line = self._line(data)
        self.assertEqual(line.shop_stock_qty, 0)
        self.assertTrue(line.shop_stock_reported)
        self.assertEqual(self._stock_warning_ids(data), [])
        self.assertTrue(self._order(data).message_ids.filtered(
            lambda msg: "pide 1, en tienda 0" in str(msg.body)
        ))

    def test_missing_shop_stock_is_warning(self):
        data, status = self._call(self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 1, "stock_tienda": None},
            {"id_articulo": self.unlinked.id, "unidades": 1, "stock_tienda": "4"},
        ]))
        self.assertEqual(status, 200, data)
        line = self._line(data)
        self.assertFalse(line.shop_stock_reported)
        self.assertEqual(self._stock_warning_ids(data), [self.product.id])
        self.assertEqual(self._line(data, self.unlinked).shop_stock_qty, 4)
        self.assertTrue(self._order(data).message_ids.filtered(
            lambda msg: "Vela PDA: pide 1, en tienda sin indicar" in str(msg.body)
        ))

    def test_rejects_invalid_shop_stock(self):
        for value in (-1, "abc", True, [], float("inf"), float("nan")):
            data, status = self._call(self._payload(lineas=[
                {"id_articulo": self.product.id, "unidades": 1, "stock_tienda": value},
            ]))
            self.assertEqual(status, 400, (value, data))
            self.assertEqual(data["code"], "INVALID_DEMAND")
            self.assertIn(str(self.product.id), data["message"])

    def test_repeated_product_uses_reported_shop_stock(self):
        data, status = self._call(self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 2, "stock_tienda": 7},
            {"id_articulo": self.product.id, "unidades": 3},
        ]))
        self.assertEqual(status, 200, data)
        line = self._line(data)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.product_qty, 5)
        self.assertEqual(line.shop_stock_qty, 7)
        self.assertTrue(line.shop_stock_reported)

    def test_repeated_product_last_reported_stock_wins(self):
        data, _status = self._call(self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 2, "stock_tienda": 7},
            {"id_articulo": self.product.id, "unidades": 3, "stock_tienda": 4},
        ]))
        self.assertEqual(self._line(data).shop_stock_qty, 4)

    def test_duplicate_does_not_update_shop_stock(self):
        payload = self._payload(lineas=[
            {"id_articulo": self.product.id, "unidades": 2, "stock_tienda": 3},
        ])
        first, _status = self._call(payload)
        payload["lineas"][0]["stock_tienda"] = 9
        payload["lineas"][0]["unidades"] = 8
        second, status = self._call(payload)
        self.assertEqual(status, 200)
        self.assertEqual(second["code"], "DUPLICATE")
        self.assertEqual(second["order_id"], first["order_id"])
        line = self._line(first)
        self.assertEqual((line.product_qty, line.shop_stock_qty), (2, 3))

    def test_manual_shop_stock_edits(self):
        order = self.env["purchase.order"].create({
            "partner_id": self.supplier.id,
            "order_line": [
                Command.create({"product_id": self.product.id, "product_qty": 1}),
                Command.create({
                    "product_id": self.unlinked.id, "product_qty": 1, "shop_stock_qty": 2,
                }),
            ],
        })
        manual, with_stock = order.order_line
        self.assertFalse(manual.shop_stock_reported)
        self.assertTrue(with_stock.shop_stock_reported)
        manual.shop_stock_qty = 0
        self.assertTrue(manual.shop_stock_reported)
        self.assertFalse(manual.copy({"order_id": order.id}).shop_stock_reported)

    def test_purchase_form_shows_shop_stock(self):
        order = self.env["purchase.order"].create({"partner_id": self.supplier.id})
        arch = order.get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('name="shop_stock_qty"', arch)
        with Form(order) as order_form:
            with order_form.order_line.new() as line:
                line.product_id = self.product
                line.product_qty = 2
                line.shop_stock_qty = 6
        self.assertEqual(order.order_line.shop_stock_qty, 6)
        self.assertTrue(order.order_line.shop_stock_reported)
