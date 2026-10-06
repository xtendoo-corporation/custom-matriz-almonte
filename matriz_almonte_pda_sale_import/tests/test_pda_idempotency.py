# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Tests de idempotencia, importes, diferido y conciliación del endpoint PDA."""

import json
from unittest.mock import patch
from uuid import uuid4

import psycopg2

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from .test_pda_sale_import import _FakeRequest


class TestPdaOrderContract(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tienda = cls.env["pos.config"].search([], limit=1)
        if not cls.tienda:
            cls.tienda = cls.env["pos.config"].create({"name": "Punto de Venta Test"})
        cls.session = cls.env["pos.session"].search(
            [("config_id", "=", cls.tienda.id), ("state", "=", "opened")], limit=1
        )
        if not cls.session:
            cls.session = cls.env["pos.session"].create(
                {
                    "config_id": cls.tienda.id,
                    "user_id": cls.env.user.id,
                    "state": "opened",
                }
            )
        cls.token = cls.env["matriz.almonte.api.token"].create(
            {
                "name": "Token Contrato PDA",
                "tienda_id": cls.tienda.id,
                "sale_user_id": cls.env.user.id,
            }
        )
        cls.product = cls.env["product.product"].search(
            [("active", "=", True), ("sale_ok", "=", True)], limit=1
        )
        cls.headers = {"Authorization": f"Bearer {cls.token.token}"}

    # -- helpers --------------------------------------------------------
    def _ctrl(self):
        from ..controllers import pda_pos_order_controller as mod

        return mod, mod.MatrizAlmontePdaPosOrderController()

    def _call(self, method, payload=None, args=None, headers=True):
        mod, controller = self._ctrl()
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        fake = _FakeRequest(
            self.env,
            headers=self.headers if headers else {},
            body=body,
        )
        fake.httprequest.args = args or {}
        # Los tests no pueden hacer commit: se sustituye (el controlador lo
        # llama tras crear el pedido en el modo de facturación diferida).
        with patch.object(mod, "request", fake), patch.object(self.env.cr, "commit"):
            response = getattr(controller, method)()
        return json.loads(response.get_data(as_text=True)), response.status_code

    def _payload(self, ref=None, **extra):
        ref = ref or f"PDA-POS-ORDER-20261005-{uuid4()}"
        payload = {
            "external_reference": ref,
            "uuid": ref,
            "lineas": [
                {"product_id": self.product.id, "qty": 2, "price_unit": 12.1},
                {"product_id": self.product.id, "qty": 1, "price_unit": 3.3},
            ],
        }
        payload.update(extra)
        return payload

    def _create(self, payload=None):
        return self._call("pda_create_pos_order", payload or self._payload())

    def _count(self, ref):
        return self.env["pos.order"].search_count(
            [("pda_external_reference", "=", ref)]
        )

    # -- respuesta ------------------------------------------------------
    def test_01_response_has_contract_fields(self):
        data, status = self._create()
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        for key in (
            "order_id",
            "order_name",
            "external_reference",
            "duplicate",
            "amount_total",
            "amount_mismatch",
            "printed",
            "print_queued",
            "print_error",
            "invoice_pending",
            "timings_ms",
        ):
            self.assertIn(key, data)
        self.assertFalse(data["duplicate"])
        self.assertIn("total", data["timings_ms"])
        self.assertIn("create", data["timings_ms"])

    # -- idempotencia ---------------------------------------------------
    def test_02_same_reference_twice_creates_one_order(self):
        payload = self._payload()
        first, _ = self._create(payload)
        second, status = self._create(payload)
        self.assertEqual(status, 200)
        self.assertTrue(second["success"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["order_id"], first["order_id"])
        self.assertEqual(second["amount_total"], first["amount_total"])
        self.assertEqual(self._count(payload["external_reference"]), 1)

    def test_03_same_reference_other_uuid_is_duplicate(self):
        payload = self._payload()
        first, _ = self._create(payload)
        other = dict(payload, uuid=str(uuid4()))
        second, status = self._create(other)
        self.assertEqual(status, 200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["order_id"], first["order_id"])

    def test_04_duplicate_with_different_amount_flags_mismatch(self):
        payload = self._payload()
        first, _ = self._create(payload)
        second, _ = self._create(dict(payload, amount_total=first["amount_total"] + 5))
        self.assertTrue(second["duplicate"])
        self.assertTrue(second["amount_mismatch"])
        self.assertEqual(second["order_id"], first["order_id"])

    def test_05_db_unique_constraint_on_reference(self):
        data, _ = self._create()
        order = self.env["pos.order"].browse(data["order_id"])
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                order.copy({"pda_external_reference": order.pda_external_reference})

    def test_06_race_integrity_error_returns_existing(self):
        """Dos peticiones simultáneas: la perdedora devuelve el pedido existente."""
        mod, controller = self._ctrl()
        payload = self._payload()
        first, _ = self._create(payload)
        existing = self.env["pos.order"].browse(first["order_id"])
        empty = self.env["pos.order"]
        fake = _FakeRequest(
            self.env, headers=self.headers, body=json.dumps(payload).encode()
        )
        with patch.object(mod, "request", fake), patch.object(
            self.env.cr, "commit"
        ), patch.object(
            mod.MatrizAlmontePdaPosOrderController,
            "_find_existing_order",
            side_effect=[empty, existing],
        ), mute_logger("odoo.sql_db"):
            response = controller.pda_create_pos_order()
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["duplicate"])
        self.assertEqual(data["order_id"], first["order_id"])
        self.assertEqual(self._count(payload["external_reference"]), 1)

    # -- importes -------------------------------------------------------
    def test_07_amount_mismatch_flagged_not_rejected(self):
        payload = self._payload(amount_total=9999.99)
        data, status = self._create(payload)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["amount_mismatch"])
        order = self.env["pos.order"].browse(data["order_id"])
        self.assertTrue(order.pda_amount_mismatch)
        self.assertEqual(order.pda_payload_amount_total, 9999.99)

    def test_08_matching_amount_not_flagged(self):
        first, _ = self._create()
        payload = self._payload(amount_total=first["amount_total"])
        data, _ = self._create(payload)
        self.assertFalse(data["amount_mismatch"])

    # -- errores --------------------------------------------------------
    def test_09_invalid_payload_creates_nothing(self):
        ref = f"PDA-POS-ORDER-20261005-{uuid4()}"
        data, status = self._create({"external_reference": ref, "lineas": []})
        self.assertEqual(status, 400)
        self.assertFalse(data["success"])
        self.assertTrue(data["code"])
        self.assertEqual(self._count(ref), 0)

    def test_10_unknown_product_code(self):
        payload = self._payload()
        payload["lineas"] = [{"default_code": "NO-EXISTE-XYZ-000", "qty": 1}]
        data, status = self._create(payload)
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "UNKNOWN_PRODUCT")
        self.assertEqual(self._count(payload["external_reference"]), 0)

    def test_11_bad_payment_code(self):
        payload = self._payload(payments=[{"payment_method_id": 99999999, "amount": 5}])
        data, status = self._create(payload)
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "BAD_PAYMENT")
        self.assertEqual(self._count(payload["external_reference"]), 0)

    def test_12_print_failure_does_not_fail_sale(self):
        mod, _ctrl = self._ctrl()
        payload = self._payload(is_printer=True)
        with patch.object(
            mod.MatrizAlmontePdaPosOrderController,
            "_dispatch_order_print",
            side_effect=RuntimeError("impresora apagada"),
        ):
            data, status = self._create(payload)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertFalse(data["printed"])
        self.assertIn("impresora apagada", data["print_error"])
        self.assertEqual(self._count(payload["external_reference"]), 1)

    # -- facturación diferida ------------------------------------------
    def _set_defer(self, value):
        self.env["ir.config_parameter"].sudo().set_param(
            "matriz_almonte_pda_sale_import.defer_invoice", value
        )

    def test_13_deferred_invoice_flow(self):
        self._set_defer("1")
        commit_patcher = patch.object(self.env.cr, "commit")
        commit_patcher.start()
        self.addCleanup(commit_patcher.stop)
        Order = type(self.env["pos.order"])
        calls = []

        def _ok(order):
            calls.append(order.id)
            return order.account_move

        data, status = self._create()
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["invoice_pending"])
        order = self.env["pos.order"].browse(data["order_id"])
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.pda_invoice_state, "pending")
        self.assertFalse(order.account_move)

        # 1.er intento: falla → el pedido sigue pagado, error visible.
        with patch.object(
            Order,
            "matriz_almonte_generate_picking_and_invoice",
            side_effect=RuntimeError("diario sin configurar"),
        ), mute_logger("odoo.addons.matriz_almonte_pda_sale_import.models.pos_order"):
            self.env["pos.order"]._cron_pda_process_deferred_invoices()
        order.invalidate_recordset()
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.pda_invoice_state, "error")
        self.assertEqual(order.pda_invoice_attempts, 1)
        self.assertIn("diario sin configurar", order.pda_invoice_error)

        # 2.º intento: funciona → facturado.
        with patch.object(
            Order, "matriz_almonte_generate_picking_and_invoice", _ok
        ):
            self.env["pos.order"]._cron_pda_process_deferred_invoices()
        order.invalidate_recordset()
        self.assertEqual(order.pda_invoice_state, "done")
        self.assertFalse(order.pda_invoice_error)
        self.assertIn(order.id, calls)

    def test_14_deferred_with_print_invoices_then_prints(self):
        self._set_defer("1")
        mod, _ctrl = self._ctrl()
        Order = type(self.env["pos.order"])
        order_of_call = []
        with patch.object(
            Order,
            "matriz_almonte_generate_picking_and_invoice",
            lambda order: order_of_call.append("invoice"),
        ), patch.object(
            mod.MatrizAlmontePdaPosOrderController,
            "_dispatch_order_print",
            lambda self_, order, cfg: order_of_call.append("print")
            or {"printed": True},
        ):
            data, status = self._create(self._payload(is_printer=True))
        self.assertEqual(status, 200)
        self.assertTrue(data["printed"])
        self.assertEqual(order_of_call, ["invoice", "print"])
        order = self.env["pos.order"].browse(data["order_id"])
        self.assertEqual(order.pda_invoice_state, "done")
        self.assertIn("invoice", data["timings_ms"])

    def test_15_deferred_invoice_failure_with_print_keeps_sale(self):
        self._set_defer("1")
        Order = type(self.env["pos.order"])
        mod, _ctrl = self._ctrl()
        with patch.object(
            Order,
            "matriz_almonte_generate_picking_and_invoice",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            mod.MatrizAlmontePdaPosOrderController,
            "_dispatch_order_print",
            return_value={"printed": False, "print_error": "sin factura"},
        ), mute_logger("odoo.addons.matriz_almonte_pda_sale_import.models.pos_order"):
            data, status = self._create(self._payload(is_printer=True))
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["invoice_pending"])
        self.assertFalse(data["printed"])

    def test_16_flag_off_keeps_legacy_atomic_behaviour(self):
        self._set_defer("")
        Order = type(self.env["pos.order"])
        payload = self._payload()
        with patch.object(
            Order,
            "matriz_almonte_generate_picking_and_invoice",
            side_effect=UserError("boom"),
        ):
            data, status = self._call("pda_create_pos_order", payload)
        self.assertEqual(status, 400)
        self.assertFalse(data["success"])
        self.assertEqual(self._count(payload["external_reference"]), 0)

    # -- conciliación ---------------------------------------------------
    def test_17_summary_matches_orders(self):
        refs = []
        for _i in range(3):
            data, _ = self._create()
            refs.append(data["external_reference"])
        summary, status = self._call("pda_pos_summary")
        self.assertEqual(status, 200)
        self.assertTrue(summary["success"])
        for ref in refs:
            self.assertIn(ref, summary["references"])
        orders = self.env["pos.order"].search(
            [("pda_external_reference", "in", summary["references"])]
        )
        self.assertEqual(summary["count"], len(orders))
        self.assertAlmostEqual(
            summary["amount_total"], round(sum(orders.mapped("amount_total")), 2), 2
        )
        by = summary["by_payment"]
        self.assertAlmostEqual(
            by["cash"]["amount"] + by["card"]["amount"],
            round(sum(orders.payment_ids.mapped("amount")), 2),
            2,
        )

    def test_18_summary_requires_token_and_valid_date(self):
        data, status = self._call("pda_pos_summary", headers=False)
        self.assertEqual(status, 401)
        data, status = self._call("pda_pos_summary", args={"date": "no-es-fecha"})
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "INVALID_PAYLOAD")

    def test_19_summary_other_day_is_empty_of_new_orders(self):
        data, _ = self._create()
        summary, _ = self._call("pda_pos_summary", args={"date": "2001-01-01"})
        self.assertNotIn(data["external_reference"], summary["references"])

    def test_20_status_found_and_missing(self):
        data, _ = self._create()
        ref = data["external_reference"]
        result, status = self._call(
            "pda_pos_status", {"references": [ref, "PDA-POS-ORDER-NO-EXISTE"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["found"][ref]["order_id"], data["order_id"])
        self.assertEqual(result["missing"], ["PDA-POS-ORDER-NO-EXISTE"])

    def test_21_status_invalid_payload(self):
        result, status = self._call("pda_pos_status", {"references": []})
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "INVALID_PAYLOAD")
