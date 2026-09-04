# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Tests del módulo matriz_almonte_pda_sale_import."""

import json
from unittest.mock import patch
from xml.etree import ElementTree

import psycopg2
from werkzeug.wrappers import Response

from odoo import api
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

# ---------------------------------------------------------------------------
# Payload de referencia — formato real de la PDA
# ---------------------------------------------------------------------------
PAYLOAD_OK = {
    "id": 1,
    "usuario": "000",
    "fecha_hora": "15/02/2024 10:11:15",
    "numero_lineas": 3,
    "uuid": "9d71fd5e-c878-4f86-bfbc-febe632ff4f8",
    "imprimir": False,
    "descuento": 0,
    "fpago": "00",
    "lineas": [
        {
            "numero": 1,
            "id_articulo": "0307196",
            "unidades": -552,
            "precio": 2.42,
            "uuid": "1847731c-3581-4263-9bbb-7c4d66d972d3",
        },
        {
            "numero": 2,
            "id_articulo": "0307198",
            "unidades": -408,
            "precio": 2.42,
            "uuid": "1847731c-3581-4263-9bbb-7c4d66d972d3",
        },
        {
            "numero": 3,
            "id_articulo": "0307187",
            "unidades": 10,
            "precio": 2.42,
            "uuid": "1847731c-3581-4263-9bbb-7c4d66d972d3",
        },
    ],
}


class _FakeHttpRequest:
    """Sustituto mínimo de ``httprequest`` para tests de controlador."""

    def __init__(self, headers=None, remote_addr="127.0.0.1", body=b""):
        self.headers = headers or {}
        self.remote_addr = remote_addr
        self._body = body

    def get_data(self, as_text=False):
        return self._body.decode("utf-8") if as_text else self._body


class _FakeRequest:
    """Sustituto mínimo de ``odoo.http.request`` para tests unitarios.

    Todos los endpoints reales de estos controladores se publican con
    ``auth="none"``. En producción, ``ir_http._auth_method_none``
    reemplaza ``request.env`` por un entorno SIN usuario asociado
    (``uid=None``, ``su=False``; ver ``odoo/addons/base/models/ir_http.py``).
    Si no replicamos ese reseteo aquí, los tests usan por error el
    entorno con el usuario real de ``TransactionCase`` (con ``su``/``uid``
    válidos), lo que oculta bugs como el de
    ``ValueError: Expected singleton: res.users()`` que solo ocurren
    cuando código del controlador accede a un recordset que no está
    forzado a ``sudo()``.
    """

    def __init__(self, env, headers=None, remote_addr="127.0.0.1", body=b""):
        self.env = api.Environment(env.cr, None, env.context)
        self.httprequest = _FakeHttpRequest(
            headers=headers,
            remote_addr=remote_addr,
            body=body,
        )

    def update_env(self, user=None, context=None, su=None):
        """Réplica mínima de ``odoo.http.Request.update_env``.

        Los controladores reales usan ``request.update_env(...)`` para
        vincular la petición a un usuario real tras autenticar el token
        (ver ``pda_pos_order_controller._authenticate_token``). Sin este
        método, el ``patch.object`` de los tests no podría ejercitar ese
        camino de código.
        """
        self.env = self.env(None, user, context, su)

    @staticmethod
    def make_response(body, headers=None, status=200):
        return Response(
            body,
            status=status,
            headers=headers or [],
        )


class TestMatrizAlmontePdaSaleImport(TransactionCase):
    """Suite de tests de la importación PDA."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tienda = cls.env["pos.config"].search([], limit=1)
        if not cls.tienda:
            cls.tienda = cls.env["pos.config"].create({"name": "Punto de Venta Test"})
        cls.token = cls.env["matriz.almonte.api.token"].create(
            {
                "name": "Token Test PDA",
                "device_code": "PDA-TEST",
                "tienda_id": cls.tienda.id,
                "sale_user_id": cls.env.user.id,
            }
        )
        cls.product_export = cls.env["product.product"].search(
            [("active", "=", True), ("sale_ok", "=", True)],
            limit=1,
        )
        cls.product_without_tax = cls.env["product.product"].search(
            [
                ("active", "=", True),
                ("sale_ok", "=", True),
                ("taxes_id", "=", False),
                ("id", "!=", cls.product_export.id),
            ],
            limit=1,
        )
        if not cls.product_export:
            raise ValidationError(
                cls.env._("No hay productos vendibles activos para ejecutar los tests.")
            )

    # ------------------------------------------------------------------
    # Tests de modelo token
    # ------------------------------------------------------------------

    def test_01_token_auto_generated(self):
        """Al crear un token sin valor, se genera automáticamente."""
        self.assertTrue(self.token.token)
        self.assertGreaterEqual(len(self.token.token), 32)

    def test_01b_token_create_without_token_field_saves_ok(self):
        """Crear un token pasando solo 'name' (sin 'token') no lanza error.

        Reproduce el comportamiento del formulario web: el usuario rellena
        solo Nombre y Código de Dispositivo y pulsa Guardar. Odoo no debe
        mostrar 'Campos no válidos: Token' sino guardar y auto-generar el token.
        """
        rec = self.env["matriz.almonte.api.token"].create(
            {
                "name": "Token Solo Nombre",
                "device_code": "PDA-WEB-01",
                "tienda_id": self.tienda.id,
                "sale_user_id": self.env.user.id,
            }
        )
        # El token debe haberse generado automáticamente
        self.assertTrue(
            rec.token, "El token no se generó al guardar sin pasarlo explícitamente."
        )
        self.assertGreaterEqual(len(rec.token), 32)
        # El registro debe ser válido y activo
        self.assertTrue(rec.active)
        self.assertEqual(rec.name, "Token Solo Nombre")

    def test_02_token_unique_constraint(self):
        """No pueden existir dos tokens con el mismo valor."""
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            self.env["matriz.almonte.api.token"].create(
                {
                    "name": "Token Duplicado",
                    "token": self.token.token,
                    "tienda_id": self.tienda.id,
                    "sale_user_id": self.env.user.id,
                }
            )

    def test_03_token_min_length(self):
        """Un token demasiado corto lanza ValidationError."""
        with self.assertRaises(ValidationError):
            self.env["matriz.almonte.api.token"].create(
                {
                    "name": "Token Corto",
                    "token": "short",
                    "tienda_id": self.tienda.id,
                    "sale_user_id": self.env.user.id,
                }
            )

    def test_04_authenticate_valid_token(self):
        """authenticate() devuelve el token cuando es válido y activo."""
        result = self.env["matriz.almonte.api.token"].authenticate(self.token.token)
        self.assertEqual(result, self.token)

    def test_05_authenticate_invalid_token(self):
        """authenticate() devuelve vacío para un token desconocido."""
        result = self.env["matriz.almonte.api.token"].authenticate(
            "INVALID_TOKEN_VALUE_XYZ_000000000000000"
        )
        self.assertFalse(result)

    def test_06_authenticate_inactive_token(self):
        """Un token inactivo no es autenticado."""
        self.token.active = False
        result = self.env["matriz.almonte.api.token"].authenticate(self.token.token)
        self.assertFalse(result)
        self.token.active = True  # Restaurar

    def test_07_authenticate_updates_last_used_at(self):
        """authenticate() actualiza last_used_at."""
        self.env["matriz.almonte.api.token"].authenticate(self.token.token)
        self.assertIsNotNone(self.token.last_used_at)

    # ------------------------------------------------------------------
    # Tests de creación de importaciones
    # ------------------------------------------------------------------

    def test_08_create_from_payload_ok(self):
        """create_from_payload() crea cabecera + líneas correctamente."""
        import_rec, is_dup = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(PAYLOAD_OK, self.token)
        self.assertFalse(is_dup)
        self.assertEqual(import_rec.state, "received")
        self.assertEqual(
            import_rec.external_reference, "9d71fd5e-c878-4f86-bfbc-febe632ff4f8"
        )
        self.assertEqual(import_rec.token_id, self.token)
        self.assertEqual(import_rec.tienda_id, self.tienda)
        self.assertEqual(import_rec.sale_user_id, self.env.user)
        self.assertEqual(len(import_rec.line_ids), 3)

    def test_09_sequence_name_assigned(self):
        """El campo name recibe el valor de la secuencia."""
        payload = dict(PAYLOAD_OK, uuid="UUID-SEQ-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertNotEqual(import_rec.name, "Nuevo")
        self.assertIn("PDA-IMP/", import_rec.name)

    def test_10_duplicate_detection_same_uuid_same_token(self):
        """El mismo uuid + mismo token se detecta como duplicado."""
        payload = dict(PAYLOAD_OK, uuid="UUID-DUP-TEST-001")
        rec1, is_dup1 = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertFalse(is_dup1)

        rec2, is_dup2 = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertTrue(is_dup2)
        self.assertEqual(rec1, rec2)

    def test_11_duplicate_detection_by_hash(self):
        """El mismo payload exacto desde otro token también se detecta."""
        token2 = self.env["matriz.almonte.api.token"].create(
            {
                "name": "Token Test 2",
                "tienda_id": self.tienda.id,
                "sale_user_id": self.env.user.id,
            }
        )
        payload = dict(PAYLOAD_OK, uuid="UUID-HASH-TEST-001")
        rec1, is_dup1 = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertFalse(is_dup1)

        rec2, is_dup2 = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, token2
        )
        self.assertTrue(is_dup2)

    def test_12_line_fields_stored(self):
        """Los campos de las líneas se almacenan correctamente desde el JSON PDA."""
        payload = dict(PAYLOAD_OK, uuid="UUID-LINES-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        # Ordenar por sequence para acceder a la primera línea
        first_line = import_rec.line_ids.sorted("sequence")[0]
        self.assertEqual(first_line.product_code, "0307196")
        self.assertAlmostEqual(first_line.qty, -552.0)
        self.assertAlmostEqual(first_line.unit_price, 2.42)
        self.assertEqual(first_line.line_uuid, "1847731c-3581-4263-9bbb-7c4d66d972d3")

    def test_12b_line_total_is_computed_from_qty_and_price(self):
        """El total de línea = unidades x precio (menos descuento)."""
        payload = {
            "id": 2,
            "usuario": "LOCAL-01",
            "fecha_hora": "17/04/2026 15:20:55",
            "uuid": "UUID-LINE-TOTAL-TEST-001",
            "imprimir": False,
            "descuento": 0,
            "fpago": "00",
            "lineas": [
                {
                    "numero": 1,
                    "id_articulo": "ART-001",
                    "unidades": 3,
                    "precio": 12.10,
                    "line_total": 0,
                    "uuid": "line-uuid-001",
                }
            ],
        }

        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        line = import_rec.line_ids

        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.qty, 3.0)
        self.assertAlmostEqual(line.unit_price, 12.10)
        self.assertAlmostEqual(line.line_total, 36.30)

    def test_13_payload_raw_stored(self):
        """El payload JSON original se almacena en payload_raw."""
        payload = dict(PAYLOAD_OK, uuid="UUID-RAW-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertTrue(import_rec.payload_raw)
        parsed = json.loads(import_rec.payload_raw)
        self.assertEqual(parsed["uuid"], "UUID-RAW-TEST-001")

    def test_14_payload_hash_computed(self):
        """Se calcula y almacena el hash SHA-256 del payload."""
        payload = dict(PAYLOAD_OK, uuid="UUID-HASH-CHK-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertTrue(import_rec.payload_hash)
        self.assertEqual(len(import_rec.payload_hash), 64)  # SHA-256 hex

    def test_15_retry_sets_state_received(self):
        """action_retry_processing() cambia el estado de error a received."""
        payload = dict(PAYLOAD_OK, uuid="UUID-RETRY-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        import_rec.write({"state": "error", "error_message": "Error simulado"})
        self.assertEqual(import_rec.state, "error")
        import_rec.action_retry_processing()
        self.assertEqual(import_rec.state, "received")
        self.assertFalse(import_rec.error_message)

    def test_16_pda_fields_mapped(self):
        """Los campos específicos de la PDA se mapean correctamente."""
        payload = dict(PAYLOAD_OK, uuid="UUID-FIELDS-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertEqual(import_rec.salesperson_code, "000")
        self.assertEqual(import_rec.payment_method, "00")
        self.assertAlmostEqual(import_rec.global_discount, 0.0)
        self.assertFalse(import_rec.print_ticket)
        self.assertEqual(import_rec.pda_id, 1)

    def test_17_fecha_hora_parsed(self):
        """La fecha en formato DD/MM/YYYY HH:MM:SS se parsea correctamente."""
        payload = dict(PAYLOAD_OK, uuid="UUID-DATE-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        self.assertIsNotNone(import_rec.operation_datetime)
        self.assertEqual(import_rec.operation_datetime.day, 15)
        self.assertEqual(import_rec.operation_datetime.month, 2)
        self.assertEqual(import_rec.operation_datetime.year, 2024)

    def test_18_negative_units_accepted(self):
        """Las unidades negativas (ajuste de inventario) se almacenan correctamente."""
        payload = dict(PAYLOAD_OK, uuid="UUID-NEG-TEST-001")
        import_rec, _ = self.env["matriz.almonte.pda.sale.import"].create_from_payload(
            payload, self.token
        )
        negative_lines = import_rec.line_ids.filtered(lambda line: line.qty < 0)
        self.assertTrue(negative_lines)

    def test_18b_form_view_uses_mail_widgets_for_chatter(self):
        """La vista formulario debe definir el chatter con widgets mail.

        En este entorno el componente `<chatter/>` no se está resolviendo bien
        en cliente, por lo que se usa la definición explícita soportada por
        Odoo con `mail_followers`, `mail_activity` y `mail_thread`.
        """
        view = self.env.ref(
            "matriz_almonte_pda_sale_import.view_matriz_almonte_pda_sale_import_form"
        )
        arch = ElementTree.fromstring(view.arch_db)
        field_names = {
            node.attrib["name"]
            for node in arch.iter("field")
            if node.attrib.get("name")
        }
        field_widgets = {
            node.attrib["name"]: node.attrib.get("widget")
            for node in arch.iter("field")
            if node.attrib.get("name")
        }

        self.assertIsNone(arch.find(".//chatter"))
        self.assertIsNotNone(arch.find(".//div[@class='oe_chatter']"))
        self.assertIn("message_follower_ids", field_names)
        self.assertIn("activity_ids", field_names)
        self.assertIn("message_ids", field_names)
        self.assertEqual(field_widgets["message_follower_ids"], "mail_followers")
        self.assertEqual(field_widgets["activity_ids"], "mail_activity")
        self.assertEqual(field_widgets["message_ids"], "mail_thread")
        self.assertIn("device_code", field_names)
        self.assertIn("customer_reference", field_names)

    def test_18c_list_view_uses_received_decoration_and_state_badge(self):
        """La lista principal refleja el estado recibido igual que el formulario."""
        view = self.env.ref(
            "matriz_almonte_pda_sale_import.view_matriz_almonte_pda_sale_import_list"
        )
        arch = ElementTree.fromstring(view.arch_db)
        list_node = arch if arch.tag == "list" else arch.find(".//list")
        state_field = arch.find(".//field[@name='state']")

        self.assertIsNotNone(list_node)
        self.assertEqual(
            list_node.attrib.get("decoration-info"),
            "state == 'received'",
        )
        self.assertIsNotNone(state_field)
        self.assertEqual(state_field.attrib.get("widget"), "badge")

    def test_18d_line_views_match_main_line_layout(self):
        """Las vistas standalone de líneas deben seguir el mismo criterio visual."""
        list_view = self.env.ref(
            "matriz_almonte_pda_sale_import.view_matriz_almonte_pda_sale_import_line_list"
        )
        form_view = self.env.ref(
            "matriz_almonte_pda_sale_import.view_matriz_almonte_pda_sale_import_line_form"
        )
        list_arch = ElementTree.fromstring(list_view.arch_db)
        form_arch = ElementTree.fromstring(form_view.arch_db)

        list_fields = {
            node.attrib["name"]: node.attrib
            for node in list_arch.iter("field")
            if node.attrib.get("name")
        }
        form_fields = {
            node.attrib["name"]: node.attrib
            for node in form_arch.iter("field")
            if node.attrib.get("name")
        }

        self.assertEqual(list_fields["sequence"].get("string"), "Nº")
        self.assertEqual(list_fields["product_code"].get("string"), "Artículo")
        self.assertEqual(list_fields["qty"].get("string"), "Unidades")
        self.assertEqual(list_fields["unit_price"].get("string"), "Precio")
        self.assertEqual(list_fields["line_uuid"].get("optional"), "hide")
        self.assertIsNotNone(form_arch.find(".//div[@class='oe_title']"))
        self.assertEqual(form_fields["product_code"].get("readonly"), "1")
        self.assertEqual(form_fields["line_uuid"].get("readonly"), "1")
        self.assertEqual(form_fields["line_total"].get("readonly"), "1")

    def test_18e_search_view_uses_consistent_labels(self):
        """La vista de búsqueda debe usar una nomenclatura homogénea."""
        view = self.env.ref(
            "matriz_almonte_pda_sale_import.view_matriz_almonte_pda_sale_import_search"
        )
        arch = ElementTree.fromstring(view.arch_db)

        field_labels = {
            node.attrib["name"]: node.attrib.get("string")
            for node in arch.iter("field")
            if node.attrib.get("name")
        }
        filter_labels = {
            node.attrib["name"]: node.attrib.get("string")
            for node in arch.iter("filter")
            if node.attrib.get("name")
        }

        self.assertEqual(arch.attrib.get("string"), "Buscar importaciones PDA")
        self.assertEqual(field_labels["external_reference"], "UUID operación")
        self.assertEqual(field_labels["device_code"], "Código Dispositivo")
        self.assertEqual(field_labels["customer_reference"], "Referencia Cliente")
        self.assertEqual(field_labels["token_id"], "Token API")
        self.assertEqual(filter_labels["filter_error"], "Errores")
        self.assertEqual(filter_labels["filter_duplicate"], "Duplicadas")
        self.assertEqual(filter_labels["filter_received"], "Recibidas")
        self.assertEqual(filter_labels["filter_processed"], "Procesadas")
        self.assertEqual(filter_labels["filter_today"], "Recibidas Hoy")
        self.assertEqual(filter_labels["group_device"], "Código Dispositivo")
        self.assertEqual(filter_labels["group_date"], "Fecha de Recepción")
        self.assertEqual(filter_labels["group_token"], "Token API")

    def test_18f_action_help_keeps_received_import_language(self):
        """La acción principal debe mantener el mismo lenguaje funcional."""
        action = self.env.ref(
            "matriz_almonte_pda_sale_import.action_matriz_almonte_pda_sale_import"
        )

        self.assertEqual(action.name, "Importaciones PDA")
        self.assertEqual(action.context, "{'search_default_filter_received': 1}")
        self.assertIn("Aún no se han recibido importaciones", action.help)
        self.assertIn("Las importaciones recibidas aparecerán aquí", action.help)

    # ------------------------------------------------------------------
    # Tests del validador de payload (capa de lógica pura)
    # ------------------------------------------------------------------

    def _validate(self, payload):
        from ..controllers.pda_sale_import_controller import (
            MatrizAlmontePdaSaleImportController,
        )

        return MatrizAlmontePdaSaleImportController._validate_payload(payload)

    def test_19_validate_ok(self):
        """Un payload bien formado no genera errores."""
        self.assertIsNone(self._validate(PAYLOAD_OK))

    def test_20_validate_missing_uuid(self):
        """Falta uuid → error."""
        payload = {k: v for k, v in PAYLOAD_OK.items() if k != "uuid"}
        self.assertIsNotNone(self._validate(payload))

    def test_21_validate_missing_lineas(self):
        """Falta lineas → error."""
        payload = {k: v for k, v in PAYLOAD_OK.items() if k != "lineas"}
        self.assertIsNotNone(self._validate(payload))

    def test_22_validate_empty_lineas(self):
        """lineas vacío → error."""
        payload = dict(PAYLOAD_OK, lineas=[])
        self.assertIsNotNone(self._validate(payload))

    def test_23_validate_line_missing_id_articulo(self):
        """Línea sin id_articulo → error."""
        bad_line = {"unidades": 1, "precio": 1.0, "uuid": "xxx", "numero": 1}
        payload = dict(PAYLOAD_OK, lineas=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_24_validate_line_missing_unidades(self):
        """Línea sin unidades → error."""
        bad_line = {"id_articulo": "X", "precio": 1.0, "uuid": "xxx", "numero": 1}
        payload = dict(PAYLOAD_OK, lineas=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_25_validate_line_negative_price(self):
        """Precio negativo → error."""
        bad_line = {
            "id_articulo": "X",
            "unidades": 1,
            "precio": -1.0,
            "uuid": "x",
            "numero": 1,
        }
        payload = dict(PAYLOAD_OK, lineas=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_26_validate_discount_out_of_range(self):
        """Descuento > 100 → error."""
        bad_line = {
            "id_articulo": "X",
            "unidades": 1,
            "precio": 1.0,
            "discount": 110,
            "uuid": "x",
            "numero": 1,
        }
        payload = dict(PAYLOAD_OK, lineas=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def _call_connection_test_endpoint(self, headers=None):
        from ..controllers import (
            pda_connection_test_controller,
        )

        controller = (
            pda_connection_test_controller.MatrizAlmontePdaConnectionTestController()
        )
        fake_request = _FakeRequest(self.env, headers=headers)

        with patch.object(pda_connection_test_controller, "request", fake_request):
            response = controller.pda_connection_test()

        return json.loads(response.get_data(as_text=True)), response.status_code

    def _call_product_catalog_endpoint(self, headers=None):
        from ..controllers import (
            pda_product_catalog_controller,
        )

        controller = (
            pda_product_catalog_controller.MatrizAlmontePdaProductCatalogController()
        )
        fake_request = _FakeRequest(self.env, headers=headers)

        with patch.object(pda_product_catalog_controller, "request", fake_request):
            response = controller.pda_product_catalog()

        return json.loads(response.get_data(as_text=True)), response.status_code

    def test_27_connection_test_requires_token(self):
        """El test de conexión debe rechazar peticiones sin token."""
        payload, status = self._call_connection_test_endpoint()

        self.assertEqual(status, 401)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "MISSING_TOKEN")
        self.assertIsNone(payload["token_id"])

    def test_28_connection_test_accepts_bearer_token(self):
        """El endpoint confirma la conexión con un Bearer token válido."""
        payload, status = self._call_connection_test_endpoint(
            headers={"Authorization": f"Bearer {self.token.token}"}
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], "CONNECTION_OK")
        self.assertEqual(payload["token_id"], self.token.id)
        self.assertEqual(payload["token_name"], self.token.name)
        self.assertEqual(payload["device_code"], self.token.device_code)

    def test_29_connection_test_rejects_invalid_token(self):
        """El test de conexión debe rechazar tokens desconocidos."""
        payload, status = self._call_connection_test_endpoint(
            headers={"Authorization": "Bearer INVALID_TOKEN_VALUE_XYZ_000000000000000"}
        )

        self.assertEqual(status, 401)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "INVALID_TOKEN")

    def test_30_connection_test_accepts_x_api_token_header(self):
        """La cabecera X-API-Token actúa como fallback para dispositivos legacy."""
        payload, status = self._call_connection_test_endpoint(
            headers={"X-API-Token": self.token.token}
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], "CONNECTION_OK")
        self.assertEqual(payload["token_id"], self.token.id)

    def test_31_product_catalog_requires_token(self):
        """El catálogo debe rechazar peticiones sin token."""
        payload, status = self._call_product_catalog_endpoint()

        self.assertEqual(status, 401)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "MISSING_TOKEN")
        self.assertEqual(payload["productos"], [])

    def test_32_product_catalog_returns_expected_fields(self):
        """El catálogo JSON debe devolver los campos requeridos por Android."""
        payload, status = self._call_product_catalog_endpoint(
            headers={"Authorization": f"Bearer {self.token.token}"}
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], "PRODUCTS_OK")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertTrue(payload["productos"])

        export_product = payload["productos"][0]
        self.assertEqual(
            set(export_product),
            {
                "id",
                "nombre",
                "codigo_barras",
                "referencia",
                "precio_costo",
                "precio_venta",
                "porcentaje_iva",
            },
        )
        self.assertIsInstance(export_product["id"], int)
        self.assertIsInstance(export_product["nombre"], str)
        self.assertIsInstance(export_product["codigo_barras"], str)
        self.assertIsInstance(export_product["referencia"], str)
        self.assertIsInstance(export_product["porcentaje_iva"], float)

    def test_33_product_catalog_rejects_invalid_token(self):
        """El catálogo debe rechazar tokens desconocidos."""
        payload, status = self._call_product_catalog_endpoint(
            headers={"Authorization": "Bearer INVALID_TOKEN_VALUE_XYZ_000000000000000"}
        )

        self.assertEqual(status, 401)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "INVALID_TOKEN")

    def test_34_product_catalog_accepts_x_api_token_header(self):
        """La cabecera X-API-Token también permite descargar el catálogo."""
        payload, status = self._call_product_catalog_endpoint(
            headers={"X-API-Token": self.token.token}
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], "PRODUCTS_OK")

    def _call_pos_session_status_endpoint(self, headers=None):
        from ..controllers import (
            pda_pos_order_controller,
        )

        controller = pda_pos_order_controller.MatrizAlmontePdaPosOrderController()
        fake_request = _FakeRequest(self.env, headers=headers)

        with patch.object(pda_pos_order_controller, "request", fake_request):
            response = controller.pda_pos_session_status()

        return json.loads(response.get_data(as_text=True)), response.status_code

    def _call_pos_order_endpoint(self, payload, headers=None):
        from ..controllers import (
            pda_pos_order_controller,
        )

        controller = pda_pos_order_controller.MatrizAlmontePdaPosOrderController()
        fake_request = _FakeRequest(
            self.env,
            headers=headers,
            body=json.dumps(payload).encode("utf-8"),
        )

        with patch.object(pda_pos_order_controller, "request", fake_request):
            response = controller.pda_create_pos_order()

        return json.loads(response.get_data(as_text=True)), response.status_code

    def test_35_pos_session_status_requires_token(self):
        """El endpoint de estado de sesión POS debe requerir token."""
        payload, status = self._call_pos_session_status_endpoint()
        self.assertEqual(status, 401)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "MISSING_TOKEN")

    def test_36_pos_session_status_returns_required_fields(self):
        """El estado de sesión devuelve guía de campos para crear pedido."""
        payload, status = self._call_pos_session_status_endpoint(
            headers={"Authorization": f"Bearer {self.token.token}"}
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertIn(payload["code"], ("SESSION_OPEN", "SESSION_NOT_OPEN"))
        self.assertIn("required_fields", payload)
        self.assertIn("header_required", payload["required_fields"])
        self.assertIn("line_required", payload["required_fields"])
        self.assertIn(
            "external_reference", payload["required_fields"]["header_required"]
        )

    def test_37_create_pos_order_requires_open_session(self):
        """Si no hay sesión abierta, la API debe devolver SESSION_NOT_OPEN."""
        self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")]
        ).sudo().write({"state": "closed"})
        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-NO-SESSION-001",
                "lineas": [{"product_id": self.product_export.id, "qty": 1}],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 409)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "SESSION_NOT_OPEN")

    def test_38_create_pos_order_ok(self):
        """Debe crear un pos.order con líneas cuando hay sesión abierta."""
        session = self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")],
            limit=1,
        )
        if not session:
            session = self.env["pos.session"].create(
                {
                    "config_id": self.tienda.id,
                    "user_id": self.env.user.id,
                    "state": "opened",
                }
            )

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-001",
                "lineas": [
                    {
                        "product_id": self.product_export.id,
                        "qty": 2,
                        "price_unit": 10.0,
                    },
                    {"product_id": self.product_export.id, "qty": 1, "price_unit": 5.0},
                ],
                "mark_as_paid": False,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], "CREATED")
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertTrue(order.exists())
        self.assertEqual(order.session_id.id, session.id)
        self.assertEqual(order.user_id.id, self.token.sale_user_id.id)
        self.assertEqual(len(order.lines), 2)

    def test_38b_create_pos_order_defaults_to_invoiced_and_paid(self):
        """Los pedidos PDA deben quedar facturados y cobrados por defecto."""
        session = self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")],
            limit=1,
        )
        if not session:
            session = self.env["pos.session"].create(
                {
                    "config_id": self.tienda.id,
                    "user_id": self.env.user.id,
                    "state": "opened",
                }
            )

        self.assertTrue(
            session.config_id.payment_method_ids,
            "La configuración POS de pruebas necesita al menos un método de pago.",
        )

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-DEFAULT-PAID-001",
                "lineas": [{"product_id": self.product_export.id, "qty": 1}],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertTrue(order.exists())
        self.assertTrue(order.to_invoice)
        self.assertEqual(order.session_id.id, session.id)
        self.assertTrue(order.payment_ids)
        self.assertAlmostEqual(order.amount_paid, order.amount_total, places=2)

        invoice_field = next(
            (
                field_name
                for field_name in ("account_move", "account_move_id", "invoice_id")
                if field_name in order._fields
            ),
            False,
        )
        if invoice_field:
            self.assertTrue(order[invoice_field])

    def test_38c_create_pos_order_uses_header_payment_method_id(self):
        """Debe respetar payment_method_id enviado en cabecera del payload."""
        session = self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")],
            limit=1,
        )
        if not session:
            session = self.env["pos.session"].create(
                {
                    "config_id": self.tienda.id,
                    "user_id": self.env.user.id,
                    "state": "opened",
                }
            )

        self.assertTrue(
            session.config_id.payment_method_ids,
            "La configuración POS de pruebas necesita al menos un método de pago.",
        )
        method = session.config_id.payment_method_ids[0]

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-PMT-ID-001",
                "lineas": [{"product_id": self.product_export.id, "qty": 1}],
                "payment_method_id": method.id,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertTrue(order.exists())
        self.assertTrue(order.payment_ids)
        self.assertEqual(order.payment_ids[0].payment_method_id.id, method.id)

    def test_38d_create_pos_order_uses_amount_paid_for_auto_payment(self):
        """El amount_paid de cabecera se usa en el cobro automático."""
        self._ensure_open_session()
        if not self.product_without_tax:
            self.skipTest("No hay producto sin impuestos para validar amount_paid.")
        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-AMOUNT-PAID-001",
                "lineas": [
                    {
                        "product_id": self.product_without_tax.id,
                        "qty": 1,
                        "price_unit": 12.0,
                    }
                ],
                "amount_paid": 12.0,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertTrue(order.exists())
        self.assertTrue(order.payment_ids)
        self.assertAlmostEqual(order.payment_ids[0].amount, 12.0, places=2)
        self.assertAlmostEqual(order.amount_paid, 12.0, places=2)

    def test_38e_create_pos_order_uses_unique_pos_reference(self):
        """Cada pedido debe tener su propia referencia POS única."""
        self._ensure_open_session()
        payload_1, status_1 = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-REF-UNIQ-001",
                "lineas": [{"product_id": self.product_export.id, "qty": 1}],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )
        payload_2, status_2 = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-REF-UNIQ-002",
                "lineas": [{"product_id": self.product_export.id, "qty": 1}],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status_1, 200)
        self.assertEqual(status_2, 200)
        self.assertTrue(payload_1["success"])
        self.assertTrue(payload_2["success"])
        order_1 = self.env["pos.order"].browse(payload_1["order_id"])
        order_2 = self.env["pos.order"].browse(payload_2["order_id"])
        self.assertTrue(order_1.exists())
        self.assertTrue(order_2.exists())
        self.assertTrue(order_1.pos_reference)
        self.assertTrue(order_2.pos_reference)
        self.assertNotEqual(order_1.pos_reference, order_2.pos_reference)

    def test_39_create_pos_order_skips_print_when_is_printer_false(self):
        """No debe lanzar impresión física cuando is_printer viene a false."""
        self._ensure_open_session()
        from ..controllers import pda_pos_order_controller

        with patch.object(
            pda_pos_order_controller.MatrizAlmontePdaPosOrderController,
            "_dispatch_order_print",
            return_value={"printed": True},
        ) as dispatch_print:
            payload, status = self._call_pos_order_endpoint(
                payload={
                    "external_reference": "PDA-POS-ORDER-PRINT-FALSE-001",
                    "lineas": [{"product_id": self.product_export.id, "qty": 1}],
                    "is_printer": False,
                },
                headers={"Authorization": f"Bearer {self.token.token}"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["print_requested"])
        self.assertFalse(payload["printed"])
        dispatch_print.assert_not_called()

    def test_40_create_pos_order_prints_when_is_printer_true(self):
        """Debe disparar la impresión física cuando is_printer viene a true."""
        self._ensure_open_session()
        from ..controllers import pda_pos_order_controller

        with patch.object(
            pda_pos_order_controller.MatrizAlmontePdaPosOrderController,
            "_dispatch_order_print",
            return_value={
                "printed": True,
                "print_url": "http://127.0.0.1:3211/print-raw",
                "print_printer": "POS-80C",
            },
        ) as dispatch_print:
            payload, status = self._call_pos_order_endpoint(
                payload={
                    "external_reference": "PDA-POS-ORDER-PRINT-TRUE-001",
                    "lineas": [{"product_id": self.product_export.id, "qty": 1}],
                    "is_printer": True,
                },
                headers={"Authorization": f"Bearer {self.token.token}"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["print_requested"])
        self.assertTrue(payload["printed"])
        self.assertEqual(payload["print_printer"], "POS-80C")
        dispatch_print.assert_called_once()

    def test_41_create_pos_order_supports_legacy_imprimir_flag(self):
        """Debe seguir respetando el flag legado 'imprimir'."""
        self._ensure_open_session()
        from ..controllers import pda_pos_order_controller

        with patch.object(
            pda_pos_order_controller.MatrizAlmontePdaPosOrderController,
            "_dispatch_order_print",
            return_value={"printed": True},
        ) as dispatch_print:
            payload, status = self._call_pos_order_endpoint(
                payload={
                    "external_reference": "PDA-POS-ORDER-PRINT-LEGACY-001",
                    "lineas": [{"product_id": self.product_export.id, "qty": 1}],
                    "imprimir": True,
                },
                headers={"Authorization": f"Bearer {self.token.token}"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["print_requested"])
        dispatch_print.assert_called_once()

    # ------------------------------------------------------------------
    # Tests de finalización: albarán de entrega + factura simplificada
    # ------------------------------------------------------------------

    def _ensure_open_session(self):
        """Devuelve una sesión POS abierta para la tienda de pruebas."""
        session = self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")],
            limit=1,
        )
        if not session:
            session = self.env["pos.session"].create(
                {
                    "config_id": self.tienda.id,
                    "user_id": self.env.user.id,
                    "state": "opened",
                }
            )
        return session

    def test_42_paid_order_generates_simplified_invoice(self):
        """Un pedido PDA pagado genera la factura simplificada (account.move)."""
        self._ensure_open_session()

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-INVOICE-001",
                "lineas": [
                    {"product_id": self.product_export.id, "qty": 1, "price_unit": 12.0}
                ],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["invoice_id"])
        self.assertEqual(payload["invoice_name"], payload["invoice_name"])

        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertTrue(order.account_move)
        self.assertEqual(order.account_move.move_type, "out_invoice")
        self.assertEqual(order.account_move.state, "posted")
        self.assertEqual(order.account_move.id, payload["invoice_id"])

    def test_43_paid_order_generates_delivery_picking(self):
        """Un pedido PDA pagado con producto almacenable genera el albarán."""
        self._ensure_open_session()

        storable = self.env["product.product"].create(
            {
                "name": "PDA Almacenable Test",
                "type": "consu",
                "is_storable": True,
                "sale_ok": True,
                "lst_price": 8.0,
                "categ_id": self.product_export.categ_id.id,
                "taxes_id": [(6, 0, self.product_export.taxes_id.ids)],
            }
        )

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-PICKING-001",
                "lineas": [{"product_id": storable.id, "qty": 2, "price_unit": 8.0}],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["picking_ids"])

        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertTrue(order.picking_ids)
        self.assertEqual(order.picking_ids.ids, payload["picking_ids"])

    def test_44_partner_fallback_to_final_consumer(self):
        """Sin cliente ni default, se usa el Consumidor Final para facturar."""
        self._ensure_open_session()
        if "default_partner_id" in self.tienda._fields:
            self.tienda.default_partner_id = False
        consumer = self.env.ref(
            "matriz_almonte_pda_sale_import.partner_consumidor_final"
        )

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-CONSUMER-001",
                "lineas": [
                    {"product_id": self.product_export.id, "qty": 1, "price_unit": 5.0}
                ],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])

        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertEqual(order.partner_id, consumer)
        self.assertTrue(order.account_move)
        self.assertEqual(order.account_move.partner_id, consumer)

    def test_45_finalize_is_idempotent(self):
        """Reejecutar la finalización no duplica factura ni albarán."""
        self._ensure_open_session()

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-IDEMPOTENT-001",
                "lineas": [
                    {"product_id": self.product_export.id, "qty": 1, "price_unit": 7.0}
                ],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200)
        order = self.env["pos.order"].browse(payload["order_id"])
        invoice = order.account_move
        self.assertTrue(invoice)

        same_invoice = order.matriz_almonte_generate_picking_and_invoice()
        self.assertEqual(same_invoice, invoice)
        self.assertEqual(order.account_move, invoice)

    def test_46_finalize_failure_rolls_back_order(self):
        """Si falla la finalización, no debe quedar pedido ni pagos a medias."""
        self._ensure_open_session()

        def _boom(order_self):
            raise UserError("fallo simulado de facturación")

        with patch.object(
            type(self.env["pos.order"]),
            "matriz_almonte_generate_picking_and_invoice",
            _boom,
        ):
            payload, status = self._call_pos_order_endpoint(
                payload={
                    "external_reference": "PDA-POS-ROLLBACK-001",
                    "uuid": "pda-pos-rollback-uuid-001",
                    "lineas": [
                        {
                            "product_id": self.product_export.id,
                            "qty": 1,
                            "price_unit": 9.0,
                        }
                    ],
                },
                headers={"Authorization": f"Bearer {self.token.token}"},
            )

        self.assertEqual(status, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], "VALIDATION_ERROR")

        leftover = self.env["pos.order"].search(
            [("uuid", "=", "pda-pos-rollback-uuid-001")]
        )
        self.assertFalse(
            leftover,
            "El pedido debe revertirse por completo cuando falla la finalización.",
        )

    def test_47_create_pos_order_auto_payment_covers_cash_rounding_total(self):
        """El pago automático debe cubrir el total ya con "cash rounding".

        Reproduce un bug real de producción: el controlador calcula el
        total manualmente línea a línea (``_prepare_order_line_vals``)
        para poblar ``order_dict`` y, con eso, decidía el importe del
        pago automático ANTES de que ``order._compute_prices()``
        recalculase el total oficial (que si el TPV tiene activado el
        redondeo de efectivo -"cash rounding"- puede diferir unos
        céntimos del cálculo manual). El pago autogenerado se quedaba
        corto y el pedido no podía cerrarse como pagado ("los pagos
        deben cubrir el total"), aunque se pedía pagar el 100 %.
        """
        self._ensure_open_session()

        no_tax_product = self.env["product.product"].create(
            {
                "name": "Producto sin impuestos (cash rounding test)",
                "type": "consu",
                "sale_ok": True,
                "taxes_id": [(6, 0, [])],
            }
        )
        expense_account = self.env["account.account"].search(
            [("account_type", "=", "expense")], limit=1
        )
        rounding = self.env["account.cash.rounding"].create(
            {
                "name": "Redondeo 0.05 (test)",
                "rounding": 0.05,
                "strategy": "add_invoice_line",
                "rounding_method": "HALF-UP",
                "profit_account_id": expense_account.id,
                "loss_account_id": expense_account.id,
            }
        )
        self.tienda.write(
            {
                "cash_rounding": True,
                "only_round_cash_method": False,
                "rounding_method": rounding.id,
            }
        )

        # 10.03 no cae en un múltiplo de 0.05: el cálculo manual del
        # controlador daría 10.03, pero el total oficial (con cash
        # rounding HALF-UP a 0.05) debe ser 10.05.
        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-CASH-ROUNDING-001",
                "lineas": [
                    {
                        "product_id": no_tax_product.id,
                        "qty": 1,
                        "price_unit": 10.03,
                    }
                ],
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["success"], payload)

        order = self.env["pos.order"].browse(payload["order_id"])
        # "paid" o "done" (tras invoicing) según el flujo estándar de POS;
        # lo relevante es que NO se quedó en "draft" por falta de cobro.
        self.assertIn(order.state, ("paid", "done", "invoiced"))
        self.assertTrue(order.account_move)
        self.assertAlmostEqual(order.amount_total, 10.05, places=2)
        self.assertAlmostEqual(order.amount_paid, order.amount_total, places=2)

    def test_48_line_prices_from_pda_are_tax_included(self):
        """Los ``price_unit`` que envía la PDA vienen con impuestos incluidos.

        Reproduce el bug real reportado: la PDA manda precios finales al
        público (con IVA incluido) tanto en cada línea como en
        ``amount_total``/``amount_paid`` de cabecera. Antes, el
        controlador sumaba el impuesto POR ENCIMA de ``price_unit`` (como
        si fuera un precio sin impuestos), inflando el total muy por
        encima de lo que el cliente pagó realmente. Ahora debe calcular
        la base imponible hacia atrás para que la suma de líneas coincida
        con el importe que declara la PDA, incluso si el impuesto está
        configurado como "no incluido en el precio" (exclusivo) en el
        producto.
        """
        self._ensure_open_session()

        tax_21 = self.env["account.tax"].create(
            {
                "name": "IVA 21% (test incluido)",
                "amount": 21.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include_override": "tax_excluded",
            }
        )
        product_a = self.env["product.product"].create(
            {
                "name": "Producto A IVA 21% (test)",
                "type": "consu",
                "sale_ok": True,
                "default_code": "TEST-010001",
                "taxes_id": [(6, 0, [tax_21.id])],
            }
        )
        product_b = self.env["product.product"].create(
            {
                "name": "Producto B IVA 21% (test)",
                "type": "consu",
                "sale_ok": True,
                "default_code": "TEST-020002",
                "taxes_id": [(6, 0, [tax_21.id])],
            }
        )

        # Mismo payload (simplificado) que el reportado en producción:
        # 2 x 0.75 + 2 x 24.2 = 49.9, con impuestos ya incluidos.
        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-TAX-INCLUDED-001",
                "amount_total": 49.9,
                "amount_paid": 49.9,
                "lines": [
                    {
                        "default_code": product_a.default_code,
                        "qty": 2,
                        "price_unit": 0.75,
                        "discount": 0,
                    },
                    {
                        "default_code": product_b.default_code,
                        "qty": 2,
                        "price_unit": 24.2,
                        "discount": 0,
                    },
                ],
                "to_invoice": True,
                "is_printer": False,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["success"], payload)

        order = self.env["pos.order"].browse(payload["order_id"])
        # El total NO debe inflarse sumando IVA por encima: debe coincidir
        # con lo que la PDA declaró como ya-con-impuestos.
        self.assertAlmostEqual(order.amount_total, 49.9, places=2)
        self.assertAlmostEqual(order.amount_paid, order.amount_total, places=2)
        # El impuesto debe haberse extraído del total, no añadido:
        # 49.9 - 49.9/1.21 ≈ 8.66, y en ningún caso ronda los ~10.48 que
        # daría sumar un 21 % por encima de 49.9.
        self.assertAlmostEqual(order.amount_tax, 49.9 - 49.9 / 1.21, places=1)
        self.assertIn(order.state, ("paid", "done", "invoiced"))

        line_a = order.lines.filtered(lambda l: l.product_id == product_a)
        # price_unit almacenado debe quedar SIN impuestos
        # (0.75 / 1.21 ≈ 0.6198), no el precio final recibido.
        self.assertAlmostEqual(line_a.price_unit, 0.75 / 1.21, places=2)
        self.assertAlmostEqual(line_a.price_subtotal_incl, 1.5, places=2)

    # ------------------------------------------------------------------
    # Tests de pagos combinados (efectivo + tarjeta)
    # ------------------------------------------------------------------

    def _validate_pos_order_payload(self, payload):
        from ..controllers.pda_pos_order_controller import (
            MatrizAlmontePdaPosOrderController,
        )

        return MatrizAlmontePdaPosOrderController._validate_payload(payload)

    def test_49_combined_payment_with_valid_payment_method_ids(self):
        """Pago combinado con payment_method_id válidos crea 2 pos.payment
        y guarda la trazabilidad del pago combinado en el pedido."""
        self._ensure_open_session()
        session = self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")],
            limit=1,
        )
        methods = session.config_id.payment_method_ids
        self.assertGreaterEqual(
            len(methods),
            2,
            "Se necesitan al menos 2 métodos de pago para probar el pago "
            "combinado.",
        )
        cash_method = methods.filtered("is_cash_count")[:1] or methods[:1]
        card_method = (methods - cash_method)[:1] or methods[-1:]

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-COMBINED-001",
                "amount_total": 20.0,
                "amount_paid": 20.0,
                "lines": [
                    {
                        "product_id": self.product_export.id,
                        "qty": 1,
                        "price_unit": 20.0,
                        "discount": 0,
                    }
                ],
                "payments": [
                    {"payment_method_id": cash_method.id, "amount": 10.0},
                    {"payment_method_id": card_method.id, "amount": 10.0},
                ],
                "fpago": "CB",
                "payment_type": "combined",
                "to_invoice": True,
                "is_printer": False,
                "amount_card": 10.0,
                "amount_cash": 10.0,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["success"], payload)
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertEqual(len(order.payment_ids), 2)
        self.assertAlmostEqual(order.amount_paid, 20.0, places=2)
        self.assertEqual(
            set(order.payment_ids.mapped("payment_method_id.id")),
            {cash_method.id, card_method.id},
        )
        self.assertEqual(order.pda_payment_type, "combined")
        self.assertAlmostEqual(order.pda_amount_cash, 10.0, places=2)
        self.assertAlmostEqual(order.pda_amount_card, 10.0, places=2)

    def test_50_combined_payment_falls_back_when_payment_method_ids_unknown(self):
        """Si los payment_method_id de 'payments' no existen en este TPV
        (p. ej. la PDA envía ids fijos de otra tienda/entorno), se resuelve
        automáticamente efectivo/tarjeta por convención de pago combinado,
        sin que falle la creación del pedido."""
        self._ensure_open_session()
        session = self.env["pos.session"].search(
            [("config_id", "=", self.tienda.id), ("state", "=", "opened")],
            limit=1,
        )
        methods = session.config_id.payment_method_ids
        self.assertGreaterEqual(len(methods), 2)

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-COMBINED-FALLBACK-001",
                "amount_total": 20.0,
                "amount_paid": 20.0,
                "lines": [
                    {
                        "product_id": self.product_export.id,
                        "qty": 1,
                        "price_unit": 20.0,
                        "discount": 0,
                    }
                ],
                # IDs que casi con total seguridad no existen como
                # pos.payment.method en este entorno de pruebas.
                "payments": [
                    {"payment_method_id": 999901, "amount": 10.0},
                    {"payment_method_id": 999902, "amount": 10.0},
                ],
                "fpago": "CB",
                "payment_type": "combined",
                "to_invoice": True,
                "is_printer": False,
                "amount_card": 10.0,
                "amount_cash": 10.0,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["success"], payload)
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertEqual(len(order.payment_ids), 2)
        self.assertAlmostEqual(order.amount_paid, 20.0, places=2)
        used_methods = order.payment_ids.mapped("payment_method_id")
        self.assertTrue(all(m in methods for m in used_methods))
        # Uno de los métodos usados debe ser el de efectivo y otro distinto
        # (asumido como tarjeta), reproduciendo el pago combinado.
        self.assertEqual(len(set(used_methods.ids)), 2)

    def test_51_create_pos_order_from_user_example_combined_payload(self):
        """Reproduce EXACTAMENTE el JSON de ejemplo de pago combinado de la
        PDA (incluyendo los ids de método de pago 10/11 tal cual), y
        confirma que el pedido se crea y se paga correctamente gracias al
        fallback automático a efectivo/tarjeta."""
        self._ensure_open_session()

        product_iva21 = self.env["product.product"].create(
            {
                "name": "Producto IVA21 combinado (test)",
                "type": "consu",
                "sale_ok": True,
                "default_code": "PRODUCTO-IVA21",
                "barcode": "8410000000011",
                "taxes_id": [(6, 0, self.product_export.taxes_id.ids)],
            }
        )
        product_iva0 = self.env["product.product"].create(
            {
                "name": "Producto IVA0 combinado (test)",
                "type": "consu",
                "sale_ok": True,
                "default_code": "PRODUCTO-IVA0",
                "barcode": "8410000000028",
                "taxes_id": [(6, 0, [])],
            }
        )

        payload, status = self._call_pos_order_endpoint(
            payload={
                "external_reference": "PDA-POS-ORDER-20260904-123",
                "amount_total": 20.0,
                "amount_paid": 20.0,
                "lines": [
                    {
                        "default_code": "PRODUCTO-IVA21",
                        "barcode": "8410000000011",
                        "qty": 1,
                        "price_unit": 12.1,
                        "discount": 0,
                    },
                    {
                        "default_code": "PRODUCTO-IVA0",
                        "barcode": "8410000000028",
                        "qty": 1,
                        "price_unit": 7.9,
                        "discount": 0,
                    },
                ],
                "payments": [
                    {"payment_method_id": 10, "amount": 10.0},
                    {"payment_method_id": 11, "amount": 10.0},
                ],
                "fpago": "CB",
                "payment_type": "combined",
                "to_invoice": True,
                "is_printer": False,
                "amount_card": 10.0,
                "amount_cash": 10.0,
            },
            headers={"Authorization": f"Bearer {self.token.token}"},
        )

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["success"], payload)
        order = self.env["pos.order"].browse(payload["order_id"])
        self.assertEqual(len(order.lines), 2)
        self.assertEqual(len(order.payment_ids), 2)
        self.assertAlmostEqual(order.amount_total, 20.0, places=2)
        self.assertAlmostEqual(order.amount_paid, 20.0, places=2)
        self.assertEqual(order.pda_payment_type, "combined")
        self.assertAlmostEqual(order.pda_amount_cash, 10.0, places=2)
        self.assertAlmostEqual(order.pda_amount_card, 10.0, places=2)
        self.assertIn(order.state, ("paid", "done", "invoiced"))
        self.assertTrue(order.account_move)

    def test_52_validate_combined_requires_split_info(self):
        """'payment_type': 'combined' sin 'payments' ni amount_cash/
        amount_card de cabecera debe fallar la validación."""
        payload = {
            "external_reference": "PDA-VALIDATE-COMBINED-001",
            "lines": [{"product_id": 1, "qty": 1, "price_unit": 1.0}],
            "payment_type": "combined",
        }
        error = self._validate_pos_order_payload(payload)
        self.assertIsNotNone(error)
        self.assertIn("combinado", error.lower())

    def test_53_validate_combined_with_single_payment_fails(self):
        """'payment_type': 'combined' con un único pago en 'payments' debe
        fallar la validación (se requieren al menos dos)."""
        payload = {
            "external_reference": "PDA-VALIDATE-COMBINED-002",
            "lines": [{"product_id": 1, "qty": 1, "price_unit": 1.0}],
            "payments": [{"payment_method_id": 1, "amount": 10.0}],
            "payment_type": "combined",
        }
        error = self._validate_pos_order_payload(payload)
        self.assertIsNotNone(error)

    def test_54_validate_combined_amount_mismatch_fails(self):
        """La suma de 'payments' debe coincidir con amount_cash+amount_card."""
        payload = {
            "external_reference": "PDA-VALIDATE-COMBINED-003",
            "lines": [{"product_id": 1, "qty": 1, "price_unit": 1.0}],
            "payments": [
                {"payment_method_id": 1, "amount": 5.0},
                {"payment_method_id": 2, "amount": 5.0},
            ],
            "payment_type": "combined",
            "amount_cash": 10.0,
            "amount_card": 10.0,
        }
        error = self._validate_pos_order_payload(payload)
        self.assertIsNotNone(error)

    def test_55_validate_combined_ok(self):
        """Un payload de pago combinado bien formado no genera error,
        replicando el JSON de ejemplo real enviado por la PDA."""
        payload = {
            "external_reference": "PDA-POS-ORDER-20260904-123",
            "amount_total": 20.0,
            "amount_paid": 20.0,
            "lines": [
                {
                    "default_code": "PRODUCTO-IVA21",
                    "barcode": "8410000000011",
                    "qty": 1,
                    "price_unit": 12.1,
                    "discount": 0,
                },
                {
                    "default_code": "PRODUCTO-IVA0",
                    "barcode": "8410000000028",
                    "qty": 1,
                    "price_unit": 7.9,
                    "discount": 0,
                },
            ],
            "payments": [
                {"payment_method_id": 10, "amount": 10.0},
                {"payment_method_id": 11, "amount": 10.0},
            ],
            "fpago": "CB",
            "payment_type": "combined",
            "to_invoice": True,
            "is_printer": False,
            "amount_card": 10.0,
            "amount_cash": 10.0,
        }
        error = self._validate_pos_order_payload(payload)
        self.assertIsNone(error)

    def test_56_resolve_payment_selector_accepts_cb_as_card(self):
        """El código 'CB' (Tarjeta Bancaria) que envía la PDA en 'fpago'
        debe resolverse como método de pago de tarjeta (no efectivo)."""
        from ..controllers.pda_pos_order_controller import (
            MatrizAlmontePdaPosOrderController,
        )

        session = self._ensure_open_session()
        methods = session.config_id.payment_method_ids
        cash_method = methods.filtered("is_cash_count")[:1] or methods[:1]
        non_cash_methods = methods - cash_method
        if not non_cash_methods:
            self.skipTest(
                "Se necesita un método de pago no-efectivo para este test."
            )

        resolved = MatrizAlmontePdaPosOrderController._resolve_payment_selector(
            selector="CB", open_session=session
        )
        self.assertIn(resolved, non_cash_methods)


