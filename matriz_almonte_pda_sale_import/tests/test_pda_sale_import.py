# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Tests del módulo matriz_almonte_pda_sale_import."""
import json
from unittest.mock import patch
from xml.etree import ElementTree

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError
from werkzeug.wrappers import Response


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

    def __init__(self, headers=None, remote_addr="127.0.0.1"):
        self.headers = headers or {}
        self.remote_addr = remote_addr


class _FakeRequest:
    """Sustituto mínimo de ``odoo.http.request`` para tests unitarios."""

    def __init__(self, env, headers=None, remote_addr="127.0.0.1"):
        self.env = env
        self.httprequest = _FakeHttpRequest(
            headers=headers,
            remote_addr=remote_addr,
        )

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
        self.assertTrue(rec.token, "El token no se generó al guardar sin pasarlo explícitamente.")
        self.assertGreaterEqual(len(rec.token), 32)
        # El registro debe ser válido y activo
        self.assertTrue(rec.active)
        self.assertEqual(rec.name, "Token Solo Nombre")

    def test_02_token_unique_constraint(self):
        """No pueden existir dos tokens con el mismo valor."""
        with self.assertRaises(Exception):
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
        result = self.env["matriz.almonte.api.token"].authenticate("INVALID_TOKEN_VALUE_XYZ_000000000000000")
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
        self.assertEqual(import_rec.external_reference, "9d71fd5e-c878-4f86-bfbc-febe632ff4f8")
        self.assertEqual(import_rec.token_id, self.token)
        self.assertEqual(import_rec.tienda_id, self.tienda)
        self.assertEqual(import_rec.sale_user_id, self.env.user)
        self.assertEqual(len(import_rec.line_ids), 3)

    def test_09_sequence_name_assigned(self):
        """El campo name recibe el valor de la secuencia."""
        payload = dict(PAYLOAD_OK, uuid="UUID-SEQ-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertNotEqual(import_rec.name, "Nuevo")
        self.assertIn("PDA-IMP/", import_rec.name)

    def test_10_duplicate_detection_same_uuid_same_token(self):
        """El mismo uuid + mismo token se detecta como duplicado."""
        payload = dict(PAYLOAD_OK, uuid="UUID-DUP-TEST-001")
        rec1, is_dup1 = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertFalse(is_dup1)

        rec2, is_dup2 = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
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
        rec1, is_dup1 = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertFalse(is_dup1)

        rec2, is_dup2 = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, token2)
        self.assertTrue(is_dup2)

    def test_12_line_fields_stored(self):
        """Los campos de las líneas se almacenan correctamente desde el JSON PDA."""
        payload = dict(PAYLOAD_OK, uuid="UUID-LINES-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        # Ordenar por sequence para acceder a la primera línea
        first_line = import_rec.line_ids.sorted("sequence")[0]
        self.assertEqual(first_line.product_code, "0307196")
        self.assertAlmostEqual(first_line.qty, -552.0)
        self.assertAlmostEqual(first_line.unit_price, 2.42)
        self.assertEqual(first_line.line_uuid, "1847731c-3581-4263-9bbb-7c4d66d972d3")

    def test_12b_line_total_is_computed_from_qty_and_price(self):
        """El total de línea debe calcularse como unidades × precio (menos descuento)."""
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

        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        line = import_rec.line_ids

        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.qty, 3.0)
        self.assertAlmostEqual(line.unit_price, 12.10)
        self.assertAlmostEqual(line.line_total, 36.30)

    def test_13_payload_raw_stored(self):
        """El payload JSON original se almacena en payload_raw."""
        payload = dict(PAYLOAD_OK, uuid="UUID-RAW-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertTrue(import_rec.payload_raw)
        parsed = json.loads(import_rec.payload_raw)
        self.assertEqual(parsed["uuid"], "UUID-RAW-TEST-001")

    def test_14_payload_hash_computed(self):
        """Se calcula y almacena el hash SHA-256 del payload."""
        payload = dict(PAYLOAD_OK, uuid="UUID-HASH-CHK-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertTrue(import_rec.payload_hash)
        self.assertEqual(len(import_rec.payload_hash), 64)  # SHA-256 hex

    def test_15_retry_sets_state_received(self):
        """action_retry_processing() cambia el estado de error a received."""
        payload = dict(PAYLOAD_OK, uuid="UUID-RETRY-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        import_rec.write({"state": "error", "error_message": "Error simulado"})
        self.assertEqual(import_rec.state, "error")
        import_rec.action_retry_processing()
        self.assertEqual(import_rec.state, "received")
        self.assertFalse(import_rec.error_message)

    def test_16_pda_fields_mapped(self):
        """Los campos específicos de la PDA se mapean correctamente."""
        payload = dict(PAYLOAD_OK, uuid="UUID-FIELDS-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertEqual(import_rec.salesperson_code, "000")
        self.assertEqual(import_rec.payment_method, "00")
        self.assertAlmostEqual(import_rec.global_discount, 0.0)
        self.assertFalse(import_rec.print_ticket)
        self.assertEqual(import_rec.pda_id, 1)

    def test_17_fecha_hora_parsed(self):
        """La fecha en formato DD/MM/YYYY HH:MM:SS se parsea correctamente."""
        payload = dict(PAYLOAD_OK, uuid="UUID-DATE-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertIsNotNone(import_rec.operation_datetime)
        self.assertEqual(import_rec.operation_datetime.day, 15)
        self.assertEqual(import_rec.operation_datetime.month, 2)
        self.assertEqual(import_rec.operation_datetime.year, 2024)

    def test_18_negative_units_accepted(self):
        """Las unidades negativas (ajuste de inventario) se almacenan correctamente."""
        payload = dict(PAYLOAD_OK, uuid="UUID-NEG-TEST-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        negative_lines = import_rec.line_ids.filtered(lambda l: l.qty < 0)
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
        """La lista principal debe reflejar el estado recibido igual que el formulario."""
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
        from odoo.addons.matriz_almonte_pda_sale_import.controllers.pda_sale_import_controller import (
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
        bad_line = {"id_articulo": "X", "unidades": 1, "precio": -1.0, "uuid": "x", "numero": 1}
        payload = dict(PAYLOAD_OK, lineas=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_26_validate_discount_out_of_range(self):
        """Descuento > 100 → error."""
        bad_line = {
            "id_articulo": "X", "unidades": 1, "precio": 1.0,
            "discount": 110, "uuid": "x", "numero": 1
        }
        payload = dict(PAYLOAD_OK, lineas=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def _call_connection_test_endpoint(self, headers=None):
        from odoo.addons.matriz_almonte_pda_sale_import.controllers import (
            pda_connection_test_controller,
        )

        controller = (
            pda_connection_test_controller.MatrizAlmontePdaConnectionTestController()
        )
        fake_request = _FakeRequest(self.env, headers=headers)

        with patch.object(pda_connection_test_controller, "request", fake_request):
            response = controller.pda_connection_test()

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
