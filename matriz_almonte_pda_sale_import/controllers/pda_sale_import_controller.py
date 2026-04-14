# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Controlador HTTP para la API de importación PDA.

Endpoint principal:
    POST /api/matriz_almonte/pda/sale/import

Autenticación:
    Authorization: Bearer <token>

El endpoint es completamente stateless: no requiere sesión Odoo, cookies
ni login de usuario.  Cualquier error devuelve JSON estructurado con el
código HTTP adecuado.
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de validación
# ---------------------------------------------------------------------------

_REQUIRED_ROOT_FIELDS = {"external_reference", "lines"}
_REQUIRED_LINE_FIELDS = {"product_code", "qty", "unit_price"}

_MAX_PAYLOAD_BYTES = 512 * 1024  # 512 KB — protección contra payloads enormes


# ---------------------------------------------------------------------------
# Helpers de respuesta
# ---------------------------------------------------------------------------

def _json_response(data, status=200):
    """Devuelve una ``Response`` JSON con cabeceras correctas."""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return request.make_response(
        body,
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ],
        status=status,
    )


def _error(code, message, http_status=400, **extra):
    """Construye una respuesta de error homogénea."""
    payload = {
        "success": False,
        "code": code,
        "message": message,
        "record_id": None,
        "external_reference": extra.get("external_reference"),
    }
    payload.update(extra)
    return _json_response(payload, status=http_status)


def _success(record, external_reference, message="OK", is_duplicate=False):
    """Construye una respuesta de éxito homogénea."""
    payload = {
        "success": True,
        "code": "DUPLICATE" if is_duplicate else "CREATED",
        "message": message,
        "record_id": record.id,
        "external_reference": external_reference,
    }
    return _json_response(payload, status=200)


# ---------------------------------------------------------------------------
# Controlador
# ---------------------------------------------------------------------------

class MatrizAlmontePdaSaleImportController(http.Controller):
    """Controlador de la API de importación de ventas PDA."""

    _ROUTE = "/api/matriz_almonte/pda/sale/import"

    @http.route(
        _ROUTE,
        type="http",
        auth="none",       # Sin sesión Odoo — autenticación propia por token
        methods=["POST"],
        csrf=False,        # API externa: CSRF no aplica
        save_session=False,
        cors="*",          # Permitir desde cualquier origen (PDA)
    )
    def pda_sale_import(self, **kwargs):
        """Recibe, valida y almacena una operación de venta enviada por la PDA.

        Flujo:
            1. Extraer y validar Bearer token
            2. Parsear cuerpo JSON
            3. Validar estructura mínima
            4. Detectar duplicados
            5. Crear cabecera + líneas
            6. Devolver respuesta JSON homogénea
        """
        # ------------------------------------------------------------------ #
        # 1. Autenticación — Bearer token                                     #
        # ------------------------------------------------------------------ #
        auth_header = request.httprequest.headers.get("Authorization", "")
        token_value = self._extract_bearer_token(auth_header)

        # Soporte adicional: cabecera X-API-Token como fallback
        if not token_value:
            token_value = request.httprequest.headers.get("X-API-Token", "").strip()

        if not token_value:
            _logger.warning("PDA API: petición sin token desde %s", self._remote_ip())
            return _error(
                "MISSING_TOKEN",
                "Se requiere autenticación. Incluye 'Authorization: Bearer <token>'.",
                http_status=401,
            )

        token_rec = (
            request.env["matriz.almonte.api.token"]
            .sudo()
            .authenticate(token_value)
        )
        if not token_rec:
            # No loguear el token recibido para no exponerlo en logs
            _logger.warning(
                "PDA API: token inválido o inactivo desde %s", self._remote_ip()
            )
            return _error(
                "INVALID_TOKEN",
                "Token de autenticación inválido o inactivo.",
                http_status=401,
            )

        # ------------------------------------------------------------------ #
        # 2. Parseo del cuerpo JSON                                           #
        # ------------------------------------------------------------------ #
        raw_body = request.httprequest.get_data(as_text=False)

        if len(raw_body) > _MAX_PAYLOAD_BYTES:
            return _error(
                "PAYLOAD_TOO_LARGE",
                f"El payload supera el tamaño máximo permitido ({_MAX_PAYLOAD_BYTES // 1024} KB).",
                http_status=413,
            )

        if not raw_body:
            return _error("EMPTY_BODY", "El cuerpo de la petición está vacío.")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _logger.info("PDA API: JSON mal formado — %s", exc)
            return _error("INVALID_JSON", f"JSON mal formado: {exc}")

        if not isinstance(payload, dict):
            return _error("INVALID_JSON", "El payload debe ser un objeto JSON (dict).")

        # ------------------------------------------------------------------ #
        # 3. Validación de estructura                                         #
        # ------------------------------------------------------------------ #
        validation_error = self._validate_payload(payload)
        if validation_error:
            return _error("VALIDATION_ERROR", validation_error)

        external_ref = payload.get("external_reference", "")

        # ------------------------------------------------------------------ #
        # 4–5. Crear o detectar duplicado                                     #
        # ------------------------------------------------------------------ #
        try:
            import_rec, is_duplicate = (
                request.env["matriz.almonte.pda.sale.import"]
                .sudo()
                .create_from_payload(payload, token_rec)
            )
        except Exception as exc:
            _logger.exception(
                "PDA API: error inesperado procesando external_ref=%s", external_ref
            )
            return _error(
                "INTERNAL_ERROR",
                "Error interno al procesar la operación. Contacte con soporte.",
                http_status=500,
                external_reference=external_ref,
            )

        # ------------------------------------------------------------------ #
        # 6. Respuesta                                                        #
        # ------------------------------------------------------------------ #
        if is_duplicate:
            return _success(
                import_rec,
                external_ref,
                message=(
                    f"Operación ya registrada previamente (id={import_rec.id}). "
                    "No se ha creado un duplicado."
                ),
                is_duplicate=True,
            )

        return _success(
            import_rec,
            external_ref,
            message="Operación recibida y registrada correctamente.",
        )

    # ------------------------------------------------------------------ #
    # Helpers privados                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_bearer_token(auth_header):
        """Extrae el valor del token de 'Authorization: Bearer <token>'."""
        if not auth_header:
            return ""
        parts = auth_header.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return ""

    @staticmethod
    def _remote_ip():
        """Devuelve la IP del cliente de forma segura."""
        return (
            request.httprequest.headers.get("X-Forwarded-For", "")
            or request.httprequest.remote_addr
            or "unknown"
        )

    @staticmethod
    def _validate_payload(payload):
        """Valida la estructura mínima del payload.

        :returns: mensaje de error (str) si hay problema, o None si es válido.
        """
        # Campos raíz obligatorios
        for field in _REQUIRED_ROOT_FIELDS:
            if field not in payload:
                return f"Campo obligatorio ausente: '{field}'."
            if payload[field] is None or payload[field] == "":
                return f"Campo obligatorio vacío: '{field}'."

        # external_reference no vacío ya se comprobó arriba
        ext_ref = payload.get("external_reference", "")
        if not isinstance(ext_ref, str) or not ext_ref.strip():
            return "El campo 'external_reference' debe ser una cadena no vacía."

        # lines debe ser lista no vacía
        lines = payload.get("lines")
        if not isinstance(lines, list):
            return "El campo 'lines' debe ser una lista."
        if len(lines) == 0:
            return "El campo 'lines' no puede estar vacío."

        # Validar cada línea
        for idx, line in enumerate(lines, start=1):
            if not isinstance(line, dict):
                return f"La línea {idx} no es un objeto JSON válido."

            for lf in _REQUIRED_LINE_FIELDS:
                if lf not in line:
                    return f"Línea {idx}: campo obligatorio ausente '{lf}'."

            try:
                qty = float(line.get("qty", 0))
            except (TypeError, ValueError):
                return f"Línea {idx}: 'qty' debe ser un número."
            if qty <= 0:
                return f"Línea {idx}: 'qty' debe ser mayor que cero (recibido: {qty})."

            try:
                unit_price = float(line.get("unit_price", 0))
            except (TypeError, ValueError):
                return f"Línea {idx}: 'unit_price' debe ser un número."
            if unit_price < 0:
                return f"Línea {idx}: 'unit_price' no puede ser negativo."

            discount = line.get("discount", 0)
            if discount is not None:
                try:
                    discount = float(discount)
                except (TypeError, ValueError):
                    return f"Línea {idx}: 'discount' debe ser un número."
                if not (0 <= discount <= 100):
                    return f"Línea {idx}: 'discount' debe estar entre 0 y 100."

        # total_amount opcional pero si existe debe ser numérico
        total = payload.get("total_amount")
        if total is not None:
            try:
                float(total)
            except (TypeError, ValueError):
                return "'total_amount' debe ser un número."

        return None  # Sin errores
