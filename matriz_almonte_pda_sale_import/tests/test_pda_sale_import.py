# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Tests del módulo matriz_almonte_pda_sale_import."""
import json
from datetime import datetime

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


# ---------------------------------------------------------------------------
# Payload de referencia
# ---------------------------------------------------------------------------
PAYLOAD_OK = {
    "external_reference": "PDA-TEST-0001",
    "operation_datetime": "2026-04-14 10:35:00",
    "device_code": "PDA-ANDROID-01",
    "salesperson_code": "AGENTE-01",
    "customer_reference": "CLI-00023",
    "payment_method": "cash",
    "total_amount": 7.50,
    "currency": "EUR",
    "notes": "Test payload",
    "lines": [
        {
            "product_code": "ART-001",
            "description": "Coca-Cola 33cl",
            "qty": 2,
            "unit_price": 1.50,
            "discount": 0,
            "line_total": 3.00,
        },
        {
            "product_code": "ART-002",
            "description": "Bocadillo jamón",
            "qty": 1,
            "unit_price": 4.50,
            "discount": 0,
            "line_total": 4.50,
        },
    ],
}


class TestMatrizAlmontePdaSaleImport(TransactionCase):
    """Suite de tests de la importación PDA."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.token = cls.env["matriz.almonte.api.token"].create(
            {
                "name": "Token Test PDA",
                "device_code": "PDA-TEST",
            }
        )

    # ------------------------------------------------------------------
    # Tests de modelo token
    # ------------------------------------------------------------------

    def test_01_token_auto_generated(self):
        """Al crear un token sin valor, se genera automáticamente."""
        self.assertTrue(self.token.token)
        self.assertGreaterEqual(len(self.token.token), 32)

    def test_02_token_unique_constraint(self):
        """No pueden existir dos tokens con el mismo valor."""
        with self.assertRaises(Exception):
            self.env["matriz.almonte.api.token"].create(
                {"name": "Token Duplicado", "token": self.token.token}
            )

    def test_03_token_min_length(self):
        """Un token demasiado corto lanza ValidationError."""
        with self.assertRaises(ValidationError):
            self.env["matriz.almonte.api.token"].create(
                {"name": "Token Corto", "token": "short"}
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
        before = self.token.last_used_at
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
        self.assertEqual(import_rec.external_reference, "PDA-TEST-0001")
        self.assertEqual(import_rec.token_id, self.token)
        self.assertEqual(len(import_rec.line_ids), 2)

    def test_09_sequence_name_assigned(self):
        """El campo name recibe el valor de la secuencia."""
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-SEQ-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertNotEqual(import_rec.name, "Nuevo")
        self.assertIn("PDA-IMP/", import_rec.name)

    def test_10_duplicate_detection_same_external_ref_same_token(self):
        """El mismo external_reference + mismo token se detecta como duplicado."""
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-DUP-001")
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
            {"name": "Token Test 2"}
        )
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-HASH-001")
        rec1, is_dup1 = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertFalse(is_dup1)

        rec2, is_dup2 = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, token2)
        self.assertTrue(is_dup2)

    def test_12_line_fields_stored(self):
        """Los campos de las líneas se almacenan correctamente."""
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-LINES-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        line = import_rec.line_ids[0]
        self.assertEqual(line.product_code, "ART-001")
        self.assertAlmostEqual(line.qty, 2.0)
        self.assertAlmostEqual(line.unit_price, 1.50)
        self.assertAlmostEqual(line.line_total, 3.00)

    def test_13_payload_raw_stored(self):
        """El payload JSON original se almacena en payload_raw."""
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-RAW-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertTrue(import_rec.payload_raw)
        parsed = json.loads(import_rec.payload_raw)
        self.assertEqual(parsed["external_reference"], "PDA-TEST-RAW-001")

    def test_14_payload_hash_computed(self):
        """Se calcula y almacena el hash SHA-256 del payload."""
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-HASH-CHK-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        self.assertTrue(import_rec.payload_hash)
        self.assertEqual(len(import_rec.payload_hash), 64)  # SHA-256 hex

    def test_15_retry_sets_state_received(self):
        """action_retry_processing() cambia el estado de error a received."""
        payload = dict(PAYLOAD_OK, external_reference="PDA-TEST-RETRY-001")
        import_rec, _ = self.env[
            "matriz.almonte.pda.sale.import"
        ].create_from_payload(payload, self.token)
        import_rec.write({"state": "error", "error_message": "Error simulado"})
        self.assertEqual(import_rec.state, "error")
        import_rec.action_retry_processing()
        self.assertEqual(import_rec.state, "received")
        self.assertFalse(import_rec.error_message)

    # ------------------------------------------------------------------
    # Tests del validador de payload (capa de lógica pura)
    # ------------------------------------------------------------------

    def _validate(self, payload):
        from odoo.addons.matriz_almonte_pda_sale_import.controllers.pda_sale_import_controller import (
            MatrizAlmontePdaSaleImportController,
        )
        return MatrizAlmontePdaSaleImportController._validate_payload(payload)

    def test_16_validate_ok(self):
        """Un payload bien formado no genera errores."""
        self.assertIsNone(self._validate(PAYLOAD_OK))

    def test_17_validate_missing_external_reference(self):
        """Falta external_reference → error."""
        payload = {k: v for k, v in PAYLOAD_OK.items() if k != "external_reference"}
        self.assertIsNotNone(self._validate(payload))

    def test_18_validate_missing_lines(self):
        """Falta lines → error."""
        payload = {k: v for k, v in PAYLOAD_OK.items() if k != "lines"}
        self.assertIsNotNone(self._validate(payload))

    def test_19_validate_empty_lines(self):
        """lines vacío → error."""
        payload = dict(PAYLOAD_OK, lines=[])
        self.assertIsNotNone(self._validate(payload))

    def test_20_validate_line_missing_product_code(self):
        """Línea sin product_code → error."""
        bad_line = {"qty": 1, "unit_price": 1.0}
        payload = dict(PAYLOAD_OK, lines=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_21_validate_line_qty_zero(self):
        """Línea con qty=0 → error."""
        bad_line = {"product_code": "X", "qty": 0, "unit_price": 1.0}
        payload = dict(PAYLOAD_OK, lines=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_22_validate_line_negative_price(self):
        """Precio negativo → error."""
        bad_line = {"product_code": "X", "qty": 1, "unit_price": -1.0}
        payload = dict(PAYLOAD_OK, lines=[bad_line])
        self.assertIsNotNone(self._validate(payload))

    def test_23_validate_discount_out_of_range(self):
        """Descuento > 100 → error."""
        bad_line = {"product_code": "X", "qty": 1, "unit_price": 1.0, "discount": 110}
        payload = dict(PAYLOAD_OK, lines=[bad_line])
        self.assertIsNotNone(self._validate(payload))
