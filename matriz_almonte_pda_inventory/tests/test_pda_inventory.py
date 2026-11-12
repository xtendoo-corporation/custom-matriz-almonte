# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import json
from datetime import datetime
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
    "fecha_hora_inicio": "09/11/2026 09:12:40",
    "fecha_hora_envio": "09/11/2026 10:35:12",
    "comentario": "Recuento de la sección de frutería, falta la cámara 2",
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
                "tz": "Europe/Madrid",
                "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        Token = cls.env["matriz.almonte.api.token"]
        cls.token = Token.create(
            {
                "name": "PDA Inventario",
                "tienda_id": cls.pos.id,
                "sale_user_id": cls.seller.id,
            }
        )
        # Segunda PDA de la misma tienda.
        cls.token2 = Token.create(
            {
                "name": "PDA Almacén",
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

    def _call(self, payload, headers=None, token=None):
        if headers is None:
            headers = {"Authorization": f"Bearer {(token or self.token).token}"}
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

    @staticmethod
    def _send(lineas, uuid=None, **extra):
        payload = dict(extra, lineas=[
            {"id_articulo": ref, "unidades": qty} for ref, qty in lineas
        ])
        if uuid:
            payload["uuid"] = uuid
        return payload

    def _logs(self, product=None):
        domain = [("location_id", "=", self.stock.id)]
        if product:
            domain.append(("product_id", "=", product.id))
        return self.env["matriz.almonte.pda.inventory.log"].search(domain, order="id")

    def _assert_pending(self, product, on_hand, counted):
        quant = self._quant(product)
        self.assertEqual(len(quant), 1)
        self.assertEqual(quant.quantity, on_hand, "el stock no se aplica todavía")
        self.assertTrue(quant.inventory_quantity_set)
        self.assertEqual(quant.inventory_quantity, counted)
        self.assertEqual(quant.inventory_diff_quantity, counted - on_hand)
        self.assertEqual(quant.user_id, self.seller)

    def _zeroings(self):
        return self.env["matriz.almonte.pda.inventory.zeroing"].search(
            [("location_id", "=", self.stock.id)]
        )

    def _run_zeroing(self, limit=None):
        Zeroing = self.env["matriz.almonte.pda.inventory.zeroing"]
        for _i in range(20):
            if not Zeroing.search_count([("state", "=", "pending")]):
                break
            Zeroing._cron_process(limit=limit)

    def _apply_all(self):
        self.env["stock.quant"]._pda_pending_quants(self.stock).action_apply_inventory()

    def test_first_send_opens_an_inventory(self):
        data, status = self._call(PDA_PAYLOAD)
        self.assertEqual(status, 200, data)
        self.assertIs(data["success"], True)
        self.assertEqual(data["message"], "Inventario registrado")
        self.assertIsInstance(data["order_id"], int)
        self.assertEqual(data["order_name"], "INVENTORY/%05d" % data["order_id"])
        self.assertEqual(data["code"], "INVENTORY_COUNTED")
        self.assertEqual(data["almacen"], self.shop.name)
        self.assertEqual(data["articulos_contados"], 2)
        self.assertNotIn("articulos_a_cero", data)
        self.assertIs(data["duplicado"], False)
        self.assertEqual(data["avisos"], [])
        self._assert_pending(self.ref1, on_hand=10, counted=12)
        self._assert_pending(self.ref2, on_hand=0, counted=3.5)
        # Lo no enviado no se toca al recibir: se pone a 0 al cerrar.
        self.assertFalse(self._quant(self.other).inventory_quantity_set)
        number = data["order_name"]
        self.assertEqual(
            set((self._quant(self.ref1) | self._quant(self.ref2)).mapped("pda_count_origin")),
            {number},
        )
        logs = self._logs()
        self.assertEqual(
            {(log.product_id, log.quantity, log.counted_after, log.name) for log in logs},
            {(self.ref1, 12, 12, number), (self.ref2, 3.5, 3.5, number)},
        )
        self.assertEqual(logs.token_id, self.token)
        self.assertEqual(set(logs.mapped("pda_uuid")), {PDA_PAYLOAD["uuid"]})
        self.assertEqual(logs.mapped("id_articulo"), ["REF001", "REF002"])
        self.assertEqual(self._quant(self.ref1).pda_log_count, 1)
        # No se crea ningún movimiento ni puesta a 0 hasta aplicar.
        self.assertFalse(self.env["stock.move"].search(
            [("is_inventory", "=", True), ("product_id", "in", [self.ref1.id, self.ref2.id])]
        ))
        self.assertFalse(self._zeroings())

    def test_next_sends_add_units_to_the_open_inventory(self):
        first, _s = self._call(self._send([("REF001", 5), ("REF002", 1)], uuid="a"))
        second, _s = self._call(self._send([("REF001", 5)], uuid="b"))
        # Otra PDA de la misma tienda suma al mismo inventario.
        third, _s = self._call(self._send([("REF001", 2), ("R-01", 4)], uuid="c"),
                               token=self.token2)
        self.assertEqual(first["order_name"], second["order_name"])
        self.assertEqual(first["order_name"], third["order_name"])
        self._assert_pending(self.ref1, on_hand=10, counted=12)
        self._assert_pending(self.ref2, on_hand=0, counted=1)
        self._assert_pending(self.other, on_hand=7, counted=4)
        logs = self._logs(self.ref1)
        self.assertEqual(logs.mapped("quantity"), [5, 5, 2])
        self.assertEqual(logs.mapped("counted_after"), [5, 10, 12])
        self.assertEqual(logs.mapped("token_id"), self.token | self.token2)
        self.assertEqual(self._quant(self.ref1).pda_log_count, 3)
        action = self._quant(self.ref1).action_view_pda_logs()
        self.assertEqual(self.env[action["res_model"]].search(action["domain"]), logs)
        self._quant(self.ref1).action_apply_inventory()
        self.assertEqual(self._quant(self.ref1).quantity, 12)

    def test_new_number_once_everything_is_applied(self):
        first, _s = self._call(self._send([("REF001", 1), ("REF002", 1)], uuid="a"))
        # Aplicar una parte no cierra el inventario.
        self._quant(self.ref1).action_apply_inventory()
        self.assertFalse(self._zeroings())
        second, _s = self._call(self._send([("REF001", 2)], uuid="b"))
        self.assertEqual(second["order_name"], first["order_name"])
        self._assert_pending(self.ref1, on_hand=1, counted=2)
        # Sin líneas pendientes en la ubicación, el siguiente envío abre otro.
        self._apply_all()
        self.assertEqual(len(self._zeroings()), 1)
        third, _s = self._call(self._send([("REF001", 3)], uuid="c"))
        self.assertEqual(third["order_id"], first["order_id"] + 1)
        self._assert_pending(self.ref1, on_hand=2, counted=3)
        self.assertEqual(self._quant(self.ref1).pda_log_count, 1)

    def test_each_location_has_its_own_inventory(self):
        other_shop = self.env["stock.warehouse"].create({"name": "Otra tienda", "code": "OTR"})
        other_pos = self.env["pos.config"].create(
            {"name": "TPV Otra", "picking_type_id": other_shop.pos_type_id.id}
        )
        other_token = self.env["matriz.almonte.api.token"].create(
            {"name": "PDA Otra", "tienda_id": other_pos.id, "sale_user_id": self.seller.id}
        )
        first, _s = self._call(self._send([("REF001", 1)], uuid="a"))
        second, _s = self._call(self._send([("REF001", 1)], uuid="a"), token=other_token)
        self.assertNotEqual(first["order_name"], second["order_name"])
        self.assertIs(second["duplicado"], False)
        self.assertEqual(second["ubicacion"], other_shop.lot_stock_id.complete_name)

    def test_repeated_send_is_not_added_twice(self):
        first, _s = self._call(PDA_PAYLOAD)
        again, status = self._call(PDA_PAYLOAD)
        self.assertEqual(status, 200, again)
        self.assertIs(again["duplicado"], True)
        self.assertEqual(again["order_name"], first["order_name"])
        self.assertEqual(again["articulos_contados"], 0)
        self._assert_pending(self.ref1, on_hand=10, counted=12)
        self.assertEqual(len(self._logs()), 2)
        # Mismo uuid con otras líneas: es otro envío y se suma.
        changed = dict(PDA_PAYLOAD, lineas=[{"id_articulo": "REF001", "unidades": 1}])
        data, _s = self._call(changed)
        self.assertIs(data["duplicado"], False)
        self._assert_pending(self.ref1, on_hand=10, counted=13)
        # Sin uuid no se puede reconocer el reenvío: se suma.
        self._call(self._send([("REF001", 1)]))
        self._call(self._send([("REF001", 1)]))
        self._assert_pending(self.ref1, on_hand=10, counted=15)

    def test_repeated_lines_in_one_send_are_summed(self):
        self._call({"lineas": [
            {"id_articulo": "REF001", "unidades": 2},
            {"id_articulo": "REF001", "unidades": "4"},
        ]})
        self._assert_pending(self.ref1, on_hand=10, counted=6)
        self.assertEqual(self._logs(self.ref1).mapped("quantity"), [6])

    def test_adds_to_a_manual_count_already_pending(self):
        quant = self._quant(self.ref1)
        quant.inventory_quantity = 3
        self.assertFalse(quant.pda_count_origin)
        data, _s = self._call(self._send([("REF001", 2)], uuid="a"))
        self._assert_pending(self.ref1, on_hand=10, counted=5)
        self.assertEqual(quant.pda_count_origin, data["order_name"])
        # Corregir a mano mantiene la línea en el inventario de la PDA.
        quant.inventory_quantity = 4
        self.assertEqual(quant.pda_count_origin, data["order_name"])
        self._call(self._send([("REF001", 1)], uuid="b"))
        self._assert_pending(self.ref1, on_hand=10, counted=5)

    def test_applying_from_odoo_sets_real_stock(self):
        data, _s = self._call(PDA_PAYLOAD)
        number = data["order_name"]
        quants = self.env["stock.quant"]._pda_pending_quants(self.stock)
        self.assertEqual(quants, self._quant(self.ref1) | self._quant(self.ref2))
        # Aplicar desde el wizard «Aplicar todo» (que propone su propio
        # nombre) mantiene el número del inventario.
        quants.with_context(inventory_name="Cantidad de producto actualizada").action_apply_inventory()
        self.assertEqual(self._quant(self.ref1).quantity, 12)
        self.assertEqual(self._quant(self.ref2).quantity, 3.5)
        self.assertFalse(quants.filtered("inventory_quantity_set"))
        self.assertFalse(quants.filtered("pda_count_origin"))
        # Inventario cerrado: queda abierta su puesta a 0, todavía sin hacer.
        zeroing = self._zeroings()
        self.assertEqual((zeroing.name, zeroing.state), (number, "pending"))
        self.assertEqual(zeroing.counted_product_ids, self.ref1 | self.ref2)
        self.assertEqual(self._quant(self.other).quantity, 7)
        self._run_zeroing()
        self.assertEqual((zeroing.state, zeroing.zeroed_count), ("done", 1))
        self.assertTrue(zeroing.date_done)
        self.assertEqual(self._quant(self.other).quantity, 0)
        moves = self.env["stock.move"].search(
            [("is_inventory", "=", True), ("location_dest_id", "=", self.stock.id)]
        ) | self.env["stock.move"].search(
            [("is_inventory", "=", True), ("location_id", "=", self.stock.id)]
        )
        self.assertEqual(len(moves), 3)
        self.assertEqual(set(moves.mapped("reference")), {number})
        zero_move = moves.filtered(lambda m: m.product_id == self.other)
        self.assertEqual(zero_move.product_uom_qty, 7)
        self.assertEqual(zero_move.pda_count_comment, "No estaba en el inventario: puesto a 0.")
        self.assertFalse(self._quant(self.other).pda_count_origin)
        # El registro de envíos se conserva tras aplicar.
        self.assertEqual(len(self._logs()), 2)
        # Volver a aplicar algo no repite la puesta a 0.
        self._call(self._send([("REF001", 1)], uuid="otro"))
        self.assertEqual(len(self._zeroings()), 1)

    def test_clear_removes_pda_mark(self):
        self._call(PDA_PAYLOAD)
        quant = self._quant(self.ref2)
        quant.action_clear_inventory_quantity()
        self.assertFalse(quant.inventory_quantity_set)
        self.assertFalse(quant.pda_count_origin or quant.pda_count_end or quant.pda_count_comment)

    def test_uncounted_stock_goes_to_zero_when_inventory_is_applied(self):
        Quant = self.env["stock.quant"]
        shelf = self.env["stock.location"].create(
            {"name": "Estante", "location_id": self.stock.id}
        )
        Quant._update_available_quantity(self.ref1, shelf, 4)
        Quant._update_available_quantity(self.zero, shelf, -2)
        Quant._update_available_quantity(self.ref2, shelf, 1)
        lot = self.env["stock.lot"].create(
            {"name": "L1", "product_id": self.lot_item.id}
        )
        Quant._update_available_quantity(self.lot_item, self.stock, 5, lot_id=lot)
        other_shop = self.env["stock.warehouse"].create(
            {"name": "Otra tienda", "code": "OTR"}
        )
        Quant._update_available_quantity(self.other, other_shop.lot_stock_id, 9)

        data, status = self._call(PDA_PAYLOAD)
        self.assertEqual(status, 200, data)
        # Un recuento a mano en curso, ajeno al inventario de la PDA.
        manual = Quant.search([("product_id", "=", self.ref2.id), ("location_id", "=", shelf.id)])
        manual.inventory_quantity = 1
        self._quant(self.ref1).action_apply_inventory()
        self._quant(self.ref2).action_apply_inventory()
        zeroing = self._zeroings()
        self.assertEqual(zeroing.state, "pending")
        # Por tandas de 1: cada ejecución pone a 0 una línea.
        Zeroing = self.env["matriz.almonte.pda.inventory.zeroing"]
        Zeroing._cron_process(limit=1)
        self.assertEqual((zeroing.state, zeroing.zeroed_count), ("pending", 1))
        self._run_zeroing(limit=1)
        self.assertEqual((zeroing.state, zeroing.zeroed_count), ("done", 3))

        def qty(product, location):
            return product.with_context(location=location.id).qty_available

        # Contados: lo contado; su stock en el estante también pasa a 0.
        self.assertEqual(qty(self.ref1, self.stock), 12)
        self.assertEqual(Quant.search([("product_id", "=", self.ref1.id), ("location_id", "=", shelf.id)]).quantity, 0)
        self.assertEqual(qty(self.ref2, self.stock), 4.5)
        # No contados: a 0, también el negativo.
        self.assertEqual(qty(self.other, self.stock), 0)
        self.assertEqual(qty(self.zero, self.stock), 0)
        # Los lotes, el recuento a mano pendiente y otros almacenes no se tocan.
        self.assertEqual(Quant.search([("product_id", "=", self.lot_item.id)]).quantity, 5)
        self.assertTrue(manual.inventory_quantity_set)
        self.assertEqual(manual.quantity, 1)
        self.assertEqual(qty(self.other, other_shop.lot_stock_id), 9)

    def test_zeroing_error_can_be_retried(self):
        self._call(PDA_PAYLOAD)
        self._apply_all()
        zeroing = self._zeroings()
        model = type(self.env["stock.quant"])
        original = model._apply_inventory

        def boom(quants, date=None):
            if quants.env.context.get("pda_inventory_zeroing"):
                raise RuntimeError("boom")
            return original(quants, date=date)

        with patch.object(model, "_apply_inventory", boom):
            self._run_zeroing()
        self.assertEqual(zeroing.state, "error")
        self.assertIn("boom", zeroing.error)
        self.assertEqual(self._quant(self.other).quantity, 7)
        self.assertFalse(self._quant(self.other).inventory_quantity_set)
        zeroing.action_retry()
        self._run_zeroing()
        self.assertEqual(zeroing.state, "done")
        self.assertEqual(self._quant(self.other).quantity, 0)

    def test_nothing_countable_is_rejected(self):
        data, status = self._call({"lineas": [{"id_articulo": "NOEXISTE", "unidades": 1}]})
        self.assertEqual((status, data["code"]), (400, "INVALID_INVENTORY"))
        self.assertFalse(self._quant(self.other).inventory_quantity_set)
        self.assertFalse(self._logs())

    def test_count_info_is_kept_and_moved_to_the_adjustment(self):
        self._call(PDA_PAYLOAD)
        quant = self._quant(self.ref1)
        # Hora local de Madrid (CET, UTC+1) guardada en UTC.
        self.assertEqual(quant.pda_count_start, datetime(2026, 11, 9, 8, 12, 40))
        self.assertEqual(quant.pda_count_end, datetime(2026, 11, 9, 9, 35, 12))
        self.assertEqual(quant.pda_count_comment, PDA_PAYLOAD["comentario"])
        log = self._logs(self.ref1)
        self.assertEqual(log.pda_count_start, datetime(2026, 11, 9, 8, 12, 40))
        self.assertEqual(log.pda_count_comment, PDA_PAYLOAD["comentario"])
        quant.action_apply_inventory()
        move = self.env["stock.move"].search(
            [("is_inventory", "=", True), ("product_id", "=", self.ref1.id)]
        )
        self.assertEqual(move.pda_count_start, datetime(2026, 11, 9, 8, 12, 40))
        self.assertEqual(move.pda_count_end, datetime(2026, 11, 9, 9, 35, 12))
        self.assertEqual(move.move_line_ids.pda_count_comment, PDA_PAYLOAD["comentario"])
        self.assertFalse(quant.pda_count_comment or quant.pda_count_start)

    def test_count_info_of_several_sends_is_merged(self):
        self._call(self._send(
            [("REF001", 1)], uuid="a", fecha_hora_inicio="09/11/2026 09:00:00",
            fecha_hora_envio="09/11/2026 09:30:00", comentario="Frutería",
        ))
        self._call(self._send(
            [("REF001", 1)], uuid="b", fecha_hora_inicio="09/11/2026 09:10:00",
            fecha_hora_envio="09/11/2026 10:00:00", comentario="Cámara 2",
        ))
        self._call(self._send([("REF001", 1)], uuid="c", comentario="Frutería"))
        quant = self._quant(self.ref1)
        self.assertEqual(quant.pda_count_start, datetime(2026, 11, 9, 8, 0, 0))
        self.assertEqual(quant.pda_count_end, datetime(2026, 11, 9, 9, 0, 0))
        self.assertEqual(quant.pda_count_comment, "Frutería\nCámara 2")
        self.assertEqual(self._logs(self.ref1).mapped("pda_count_comment"),
                         ["Frutería", "Cámara 2", "Frutería"])

    def test_count_info_is_optional_and_accepts_old_format(self):
        data, status = self._call({
            "fecha_hora": "09/11/2026 10:35:12",
            "lineas": PDA_PAYLOAD["lineas"],
        })
        self.assertEqual(status, 200, data)
        quant = self._quant(self.ref1)
        self.assertFalse(quant.pda_count_start)
        self.assertEqual(quant.pda_count_end, datetime(2026, 11, 9, 9, 35, 12))
        self.assertFalse(quant.pda_count_comment)

    def test_rejects_invalid_count_info(self):
        for extra in (
            {"fecha_hora_inicio": "31/02/2026 10:00:00"},
            {"fecha_hora_envio": "ayer"},
            {"fecha_hora_envio": 20261109},
            {"fecha_hora_inicio": "09/11/2026 11:00:00",
             "fecha_hora_envio": "09/11/2026 10:00:00"},
            {"comentario": 5},
            {"comentario": "x" * 2001},
        ):
            data, status = self._call(dict({"lineas": PDA_PAYLOAD["lineas"]}, **extra))
            self.assertEqual((status, data["code"]), (400, "INVALID_INVENTORY"), extra)
        self.assertFalse(self._quant(self.ref1).inventory_quantity_set)
        self.assertFalse(self._logs())

    def test_ref_keeps_leading_zeros(self):
        data, status = self._call({"lineas": [{"id_articulo": "010001", "unidades": 1}]})
        self.assertEqual(status, 200, data)
        self._assert_pending(self.zero, on_hand=0, counted=1)
        data, status = self._call({"lineas": [
            {"id_articulo": "REF001", "unidades": 1},
            {"id_articulo": 10001, "unidades": 1},
        ]})
        self.assertEqual(data["articulos_contados"], 1)
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
        self.assertEqual(self._logs().product_id, self.ref1)

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
            model, "_pda_add_counted_quantities", side_effect=RuntimeError("boom")
        ):
            data, status = self._call(PDA_PAYLOAD)
        self.assertEqual((status, data["code"]), (500, "SERVER_ERROR"))
        self.assertFalse(self._quant(self.other).inventory_quantity_set)

    def test_stock_user_can_read_the_log(self):
        self._call(PDA_PAYLOAD)
        user = self.env["res.users"].create({
            "name": "Almacén", "login": "almacen_pda_inv",
            "group_ids": [(6, 0, [self.env.ref("stock.group_stock_user").id])],
        })
        logs = self._logs().with_user(user)
        self.assertEqual(len(logs.read(["name", "quantity"])), 2)
        self.assertEqual(self._quant(self.ref1).with_user(user).pda_log_count, 1)
