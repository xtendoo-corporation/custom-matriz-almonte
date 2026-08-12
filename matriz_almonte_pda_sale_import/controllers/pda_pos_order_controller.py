# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Controlador HTTP para consultar sesión POS y crear pedidos desde PDA."""

import json
import logging
import base64
import socket
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests as http_requests

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow siempre debería estar disponible
    Image = None

from odoo import SUPERUSER_ID, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.tools import float_compare, float_is_zero

from .pda_sale_import_controller import MatrizAlmontePdaSaleImportController

_logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 512 * 1024  # 512 KB

# Ancho por defecto (en puntos/dots) del área imprimible de una impresora
# térmica de 80 mm a 203 dpi. Es el estándar más habitual en impresoras
# ESC/POS de 80 mm (72 mm de área imprimible ≈ 576 dots).
_DEFAULT_PRINTER_WIDTH_DOTS = 576
_PDF_RASTER_DPI = 203
# Nº máximo de líneas verticales por bloque "GS v 0": trocear la imagen
# evita problemas de buffer en impresoras térmicas con tickets largos.
_ESCPOS_RASTER_CHUNK_LINES = 256


def _json_response(data, status=200):
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
    payload = {
        "success": False,
        "code": code,
        "message": message,
    }
    payload.update(extra)
    return _json_response(payload, status=http_status)


def _remote_ip():
    return (
        request.httprequest.headers.get("X-Forwarded-For", "")
        or request.httprequest.remote_addr
        or "unknown"
    )


class MatrizAlmontePdaPosOrderController(http.Controller):
    """Endpoints para integración externa de pedidos POS."""

    _STATUS_ROUTE = "/api/matriz_almonte/pda/pos/session/status"
    _ORDER_ROUTE = "/api/matriz_almonte/pda/pos/order"

    @http.route(
        _STATUS_ROUTE,
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_pos_session_status(self, **kwargs):
        """Informa si la sesión POS asociada al token está abierta."""
        token_rec, error_response = self._authenticate_token()
        if error_response:
            return error_response

        pos_config = token_rec.tienda_id
        if not pos_config:
            return _error(
                "TOKEN_WITHOUT_POS",
                "El token no tiene un Punto de Venta POS configurado.",
                http_status=400,
            )

        open_session = self._get_open_session(pos_config)
        latest_session = (
            request.env["pos.session"]
            .sudo()
            .search(
                [("config_id", "=", pos_config.id)],
                order="id desc",
                limit=1,
            )
        )
        is_open = bool(open_session)

        return _json_response(
            {
                "success": True,
                "code": "SESSION_OPEN" if is_open else "SESSION_NOT_OPEN",
                "message": (
                    "La sesión POS está abierta y disponible."
                    if is_open
                    else "La sesión POS no está abierta. Debe abrirse desde Odoo."
                ),
                "token_id": token_rec.id,
                "token_name": token_rec.name,
                "pos_config_id": pos_config.id,
                "pos_config_name": pos_config.name,
                "session_open": is_open,
                "session_id": open_session.id if open_session else False,
                "session_name": open_session.name if open_session else "",
                "session_state": (
                    open_session.state
                    if open_session
                    else (latest_session.state if latest_session else "none")
                ),
                "required_fields": self._required_payload_fields(),
            },
            status=200,
        )

    @staticmethod
    def _log_token_and_pos_info(token_rec, pos_config):
        """Registra el diagnóstico completo del token y el POS en el log."""
        _logger.info("*" * 80)
        _logger.info("📋 [PDA ORDER] INFORMACIÓN DEL TOKEN Y PUNTO DE VENTA")
        _logger.info("*" * 80)
        _logger.info("")
        _logger.info("🔑 INFORMACIÓN DEL TOKEN:")
        _logger.info(f"   ├─ ID Token: {token_rec.id}")
        _logger.info(f"   ├─ Nombre: {token_rec.name}")
        _logger.info(f"   ├─ Código Dispositivo: {token_rec.device_code or 'N/A'}")
        estado_token = "✅ ACTIVO" if token_rec.active else "❌ INACTIVO"
        _logger.info(f"   ├─ Estado: {estado_token}")
        usuario_nombre = (
            token_rec.sale_user_id.name if token_rec.sale_user_id else "N/A"
        )
        usuario_id = token_rec.sale_user_id.id if token_rec.sale_user_id else "N/A"
        _logger.info(f"   ├─ Usuario de Ventas: {usuario_nombre} (ID: {usuario_id})")
        _logger.info(f"   ├─ Último uso: {token_rec.last_used_at or 'Nunca'}")
        _logger.info(f"   ├─ Importaciones totales: {token_rec.import_count}")
        _logger.info(f"   └─ Notas: {token_rec.notes or 'Sin notas'}")
        _logger.info("")

        _logger.info("🏪 INFORMACIÓN DEL PUNTO DE VENTA:")
        _logger.info(f"   ├─ ID POS: {pos_config.id}")
        _logger.info(f"   ├─ Nombre: {pos_config.name}")
        estado_pos = "✅ ACTIVO" if pos_config.active else "❌ INACTIVO"
        _logger.info(f"   ├─ Estado: {estado_pos}")
        empresa_nombre = pos_config.company_id.name if pos_config.company_id else "N/A"
        empresa_id = pos_config.company_id.id if pos_config.company_id else "N/A"
        _logger.info(f"   ├─ Empresa: {empresa_nombre} (ID: {empresa_id})")
        almacen_nombre = (
            pos_config.warehouse_id.name if pos_config.warehouse_id else "N/A"
        )
        almacen_id = pos_config.warehouse_id.id if pos_config.warehouse_id else "N/A"
        _logger.info(f"   ├─ Almacén: {almacen_nombre} (ID: {almacen_id})")
        moneda_nombre = pos_config.currency_id.name if pos_config.currency_id else "N/A"
        _logger.info(f"   ├─ Moneda: {moneda_nombre}")
        tiene_sesion = "✅ SÍ" if pos_config.has_active_session else "❌ NO"
        _logger.info(f"   └─ Has Active Session: {tiene_sesion}")
        _logger.info("")

        _logger.info("📥 INFORMACIÓN DE IMPORTACIÓN:")
        puede_importar = (
            "✅ SÍ - Se aceptarán pedidos"
            if pos_config.active and token_rec.active
            else "❌ NO - Se rechazarán pedidos"
        )
        _logger.info(f"   ├─ ¿IMPORTA?: {puede_importar}")
        _logger.info("   │")
        if not pos_config.active:
            _logger.info("   ├─ RAZÓN: El Punto de Venta está INACTIVO")
            _logger.info("   │  └─ Acción: Activar POS en configuración")
        if not token_rec.active:
            _logger.info("   ├─ RAZÓN: El Token está INACTIVO")
            _logger.info("   │  └─ Acción: Activar token en configuración")
        if pos_config.active and token_rec.active and not pos_config.has_active_session:
            _logger.info("   ├─ RAZÓN: NO hay sesión POS abierta")
            _logger.info("   │  └─ Acción: Se validará al intentar crear pedido")
        if pos_config.active and token_rec.active and pos_config.has_active_session:
            _logger.info(
                "   ├─ RAZÓN: TODO CORRECTO - Token y POS activos con sesión abierta"
            )
            _logger.info("   │  └─ Acción: Se aceptarán los pedidos")
        estado_final = (
            "✅ APTO PARA CREAR PEDIDOS"
            if pos_config.active and token_rec.active and pos_config.has_active_session
            else "❌ NO APTO - Revisar configuración"
        )
        _logger.info(f"   └─ Estado Final: {estado_final}")
        _logger.info("*" * 80)
        _logger.info("")

    @http.route(
        _ORDER_ROUTE,
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_create_pos_order(self, **kwargs):
        """Crea un pedido POS desde un payload JSON externo."""
        _logger.info("=" * 80)
        _logger.info("=" * 80)
        _logger.info("=" * 80)
        _logger.info("=" * 80)
        _logger.info("🔵 [PDA ORDER] Nueva petición de creación de pedido desde PDA")
        _logger.info(f"   IP del cliente: {_remote_ip()}")

        token_rec, error_response = self._authenticate_token()
        if error_response:
            _logger.warning("❌ [PDA ORDER] Error de autenticación")
            return error_response

        _logger.info(
            f"✅ [PDA ORDER] Token autenticado: {token_rec.name} (ID: {token_rec.id})"
        )

        # ====== LEER JSON RAW PRIMERO ======
        payload_or_error = self._read_json_payload()
        if isinstance(payload_or_error, dict) and payload_or_error.get("_error"):
            err = payload_or_error["_error"]
            _logger.warning(
                "❌ [PDA ORDER] Error al leer payload: %s - %s",
                err["code"],
                err["message"],
            )
            return _error(
                err["code"],
                err["message"],
                http_status=err["status"],
                required_fields=self._required_payload_fields(),
            )
        payload = payload_or_error

        pos_config = token_rec.tienda_id
        if not pos_config:
            _logger.warning(
                f"❌ [PDA ORDER] Token {token_rec.name} sin POS configurado"
            )
            return _error(
                "TOKEN_WITHOUT_POS",
                "El token no tiene un Punto de Venta POS configurado.",
                http_status=400,
            )

        self._log_token_and_pos_info(token_rec, pos_config)

        # ====== VERIFICACIÓN CRÍTICA: SESIÓN POS ABIERTA ======
        _logger.info("🔍 [PDA ORDER] Verificando si sesión POS está ABIERTA...")
        _logger.info(f"   ├─ Buscando en POS: {pos_config.name}")
        _logger.info(f"   ├─ Criterios: config_id={pos_config.id} AND state='opened'")

        open_session = self._get_open_session(pos_config)

        if not open_session:
            _logger.error("*" * 80)
            _logger.error("❌ [PDA ORDER] ¡¡SESIÓN POS CERRADA O NO EXISTE!!")
            _logger.error("*" * 80)
            _logger.error("Detalles del error:")
            _logger.error(f"├─ Punto de Venta: {pos_config.name} (ID: {pos_config.id})")
            _logger.error(f"├─ Token recibido: {token_rec.name} (ID: {token_rec.id})")
            _logger.error("├─ Estado: NO HAY SESIÓN ABIERTA")
            _logger.error("└─ Acción requerida: Abrir sesión en Odoo 18 primero")
            _logger.error("*" * 80)
            return _error(
                "SESSION_NOT_OPEN",
                (
                    "❌ SESIÓN POS NO ABIERTA\n\n"
                    f"No hay ninguna sesión POS abierta para '{pos_config.name}'.\n\n"
                    "Acción requerida:\n"
                    "1. Inicia sesión en Odoo 18\n"
                    "2. Ve a Punto de Venta → {pos_config.name}\n"
                    "3. Abre una nueva sesión\n\n"
                    "Una vez abierta la sesión, puedes usar la PDA para crear pedidos."
                ),
                http_status=409,
                pos_config_id=pos_config.id,
                pos_config_name=pos_config.name,
                session_open=False,
                required_fields=self._required_payload_fields(),
            )

        _logger.info("*" * 80)
        _logger.info("✅ [PDA ORDER] ¡¡SESIÓN POS ABIERTA Y LISTA!!")
        _logger.info("*" * 80)
        _logger.info("Detalles de la sesión:")
        _logger.info(f"├─ Nombre sesión: {open_session.name}")
        _logger.info(f"├─ ID sesión: {open_session.id}")
        _logger.info(f"├─ Estado: {open_session.state} (OPENED)")
        _logger.info(f"├─ Empresa: {open_session.company_id.name}")
        _logger.info(f"└─ Usuario responsable: {open_session.user_id.name}")
        _logger.info("*" * 80)

        # Validar el payload
        validation_error = self._validate_payload(payload)
        if validation_error:
            _logger.warning(f"❌ [PDA ORDER] Validación fallida: {validation_error}")
            return _error(
                "VALIDATION_ERROR",
                validation_error,
                http_status=400,
                required_fields=self._required_payload_fields(),
            )

        _logger.info("📦 [PDA ORDER] Payload recibido:")
        _logger.info(f"   - external_reference: {payload.get('external_reference')}")
        _logger.info(f"   - uuid: {payload.get('uuid')}")
        _logger.info(f"   - partner_id: {payload.get('partner_id')}")
        _logger.info(f"   - to_invoice: {payload.get('to_invoice')}")
        _logger.info(f"   - mark_as_paid: {payload.get('mark_as_paid')}")
        _logger.info(f"   - amount_paid: {payload.get('amount_paid')}")
        _logger.info(f"   - fpago : {payload.get('fpago')}")
        _logger.info(f"   - payment_type: {payload.get('payment_type')}")
        _logger.info(f"   - is_printer: {self._is_print_requested(payload)}")

        lineas = payload.get("lineas") if "lineas" in payload else payload.get("lines")
        _logger.info(f"   - Número de líneas: {len(lineas) if lineas else 0}")
        if lineas:
            for idx, line in enumerate(lineas, start=1):
                producto = (
                    line.get("product_id")
                    or line.get("default_code")
                    or line.get("barcode")
                    or line.get("id_articulo")
                )
                precio = line.get("price_unit", line.get("precio", "default"))
                _logger.info(
                    "     Línea %s: producto=%s, qty=%s, price=%s, discount=%s%%",
                    idx,
                    producto,
                    line.get("qty"),
                    precio,
                    line.get("discount", 0),
                )

        payments = payload.get("payments", [])
        _logger.info(f"   - Número de pagos: {len(payments)}")
        if payments:
            for idx, pmt in enumerate(payments, start=1):
                _logger.info(
                    "     Pago %s: method=%s, amount=%s",
                    idx,
                    pmt.get("payment_method_id"),
                    pmt.get("amount"),
                )

        # Validar el payload
        validation_error = self._validate_payload(payload)
        if validation_error:
            _logger.warning(f"❌ [PDA ORDER] Validación fallida: {validation_error}")
            return _error(
                "VALIDATION_ERROR",
                validation_error,
                http_status=400,
                required_fields=self._required_payload_fields(),
            )

        external_ref = str(
            payload.get("external_reference") or payload.get("uuid") or ""
        ).strip()
        order_uuid = str(payload.get("uuid") or external_ref or uuid4())

        _logger.info("🔍 [PDA ORDER] Buscando pedido duplicado...")
        _logger.info(f"   - external_ref: {external_ref}")
        _logger.info(f"   - order_uuid: {order_uuid}")

        existing_order = (
            request.env["pos.order"]
            .sudo()
            .search(
                [
                    ("session_id.config_id", "=", pos_config.id),
                    ("uuid", "=", order_uuid),
                ],
                limit=1,
            )
        )
        if existing_order:
            print_requested = self._is_print_requested(payload)
            _logger.warning(
                f"⚠️  [PDA ORDER] Pedido DUPLICADO detectado: "
                f"{existing_order.name} (ID: {existing_order.id})"
            )
            return _json_response(
                {
                    "success": True,
                    "code": "DUPLICATE",
                    "message": (
                        "Ya existe un pedido POS con esa referencia "
                        f"(id={existing_order.id})."
                    ),
                    "order_id": existing_order.id,
                    "order_name": existing_order.name,
                    "external_reference": external_ref,
                    "session_id": existing_order.session_id.id,
                    "print_requested": print_requested,
                    "printed": False,
                    "print_error": (
                        "Pedido duplicado detectado. "
                        "No se reimprime automáticamente."
                    )
                    if print_requested
                    else False,
                },
                status=200,
            )

        _logger.info("📝 [PDA ORDER] Creando nuevo pedido POS...")
        try:
            # Savepoint para garantizar atomicidad: si falla la generación del
            # albarán o de la factura simplificada, se revierten también el
            # pedido y los pagos ya creados y no queda una venta a medias.
            with request.env.cr.savepoint():
                order = self._create_pos_order_from_payload(
                    payload=payload,
                    token_rec=token_rec,
                    pos_config=pos_config,
                    open_session=open_session,
                    order_uuid=order_uuid,
                    external_ref=external_ref,
                )
            _logger.info(
                "✅ [PDA ORDER] PEDIDO CREADO EXITOSAMENTE: %s (ID: %s)",
                order.name,
                order.id,
            )
            _logger.info(f"   - external_reference: {external_ref}")
            _logger.info(f"   - Total: {order.amount_total} {order.currency_id.name}")
            _logger.info(f"   - Usuario: {order.user_id.name} (ID: {order.user_id.id})")
            _logger.info(f"   - Estado: {order.state}")
            _logger.info("=" * 80)
        except (ValidationError, UserError) as exc:
            _logger.error(f"❌ [PDA ORDER] Error al crear pedido: {str(exc)}")
            _logger.info("=" * 80)
            return _error("VALIDATION_ERROR", str(exc), http_status=400)

        print_requested = self._is_print_requested(payload)
        payment = order.payment_ids[:1]
        response_payload = {
            "success": True,
            "code": "CREATED",
            "message": "Pedido POS creado correctamente.",
            "order_id": order.id,
            "order_name": order.name,
            "is_refund": order.amount_total < 0,
            "external_reference": external_ref,
            "session_id": order.session_id.id,
            "session_name": order.session_id.name,
            "session_state": order.session_id.state,
            "amount_total": order.amount_total,
            "amount_paid": order.amount_paid,
            "payment_method_id": payment.payment_method_id.id if payment else False,
            "payment_method_name": payment.payment_method_id.name if payment else False,
            "state": order.state,
            "invoice_id": order.account_move.id if order.account_move else False,
            "invoice_name": order.account_move.name if order.account_move else False,
            "simplified_invoice_number": order.pda_simplified_invoice_number or False,
            "picking_ids": order.picking_ids.ids,
            "print_requested": print_requested,
            "printed": False,
        }
        if print_requested:
            response_payload.update(self._dispatch_order_print(order, pos_config))

        return _json_response(response_payload, status=200)

    def _authenticate_token(self):
        auth_header = request.httprequest.headers.get("Authorization", "")
        token_value = MatrizAlmontePdaSaleImportController._extract_bearer_token(
            auth_header
        )
        if not token_value:
            token_value = request.httprequest.headers.get("X-API-Token", "").strip()

        if not token_value:
            _logger.warning("PDA POS API: petición sin token desde %s", _remote_ip())
            return (
                None,
                _error(
                    "MISSING_TOKEN",
                    "Se requiere autenticación. "
                    "Incluye 'Authorization: Bearer <token>'.",
                    http_status=401,
                ),
            )

        token_rec = (
            request.env["matriz.almonte.api.token"].sudo().authenticate(token_value)
        )
        if not token_rec:
            _logger.warning(
                "PDA POS API: token inválido o inactivo desde %s", _remote_ip()
            )
            return (
                None,
                _error(
                    "INVALID_TOKEN",
                    "Token de autenticación inválido o inactivo.",
                    http_status=401,
                ),
            )

        # Esta ruta se publica con auth="none", por lo que Odoo reinicia
        # `request.env` dejando `uid=None` (ver
        # `ir_http._auth_method_none`). El problema es que `sudo()` NO
        # cambia el uid, solo activa el flag `su`: `env.user` sigue
        # siendo `browse(env.uid)`, así que con `uid=None` da un
        # recordset vacío. Cualquier código -de este módulo o de
        # dependencias estándar de Odoo/OCA, p.ej.
        # ``mail.thread.message_post()`` al crear la factura
        # simplificada- que llame a ``self.env.user`` explota con
        # ``ValueError: Expected singleton: res.users()``.
        # Para evitarlo de raíz, vinculamos el resto de la petición al
        # "Usuario de Ventas" configurado en el token (obligatorio al
        # crear el token), manteniendo el modo superusuario para no
        # alterar el comportamiento de ACL/reglas de registro que ya
        # asume el resto del controlador con sudo().
        real_uid = token_rec.sale_user_id.id or SUPERUSER_ID
        request.update_env(user=real_uid, su=True)
        token_rec = token_rec.with_env(request.env)

        return token_rec, None

    @staticmethod
    def _get_open_session(pos_config):
        return (
            request.env["pos.session"]
            .sudo()
            .search(
                [("config_id", "=", pos_config.id), ("state", "=", "opened")],
                order="id desc",
                limit=1,
            )
        )

    @staticmethod
    def _required_payload_fields():
        return {
            "header_required": ["external_reference", "lineas"],
            "line_required": [
                "product_id | default_code | barcode | id_articulo",
                "qty",
            ],
            "line_optional": ["price_unit", "discount", "description", "uuid"],
            "payment_optional": ["payment_method_id", "amount", "payment_date"],
            "header_optional": [
                "uuid",
                "partner_id",
                "date_order",
                "to_invoice",
                "mark_as_paid",
                "amount_paid",
                "payment_method_id",
                "fpago",
                "payment_type",
                "is_printer",
                "imprimir",
                "payments",
            ],
        }

    @staticmethod
    def _read_json_payload():
        raw_body = request.httprequest.get_data(as_text=False)

        # Mostrar JSON raw con separador de asteriscos
        _logger.info("*" * 80)
        _logger.info("📨 [PDA ORDER] JSON RAW RECIBIDO DESDE PDA:")
        _logger.info("*" * 80)

        if not raw_body:
            _logger.warning("⚠️  [PDA ORDER] Cuerpo de petición VACÍO")
            _logger.info("*" * 80)
            return {
                "_error": {
                    "code": "EMPTY_BODY",
                    "message": "El cuerpo de la petición está vacío.",
                    "status": 400,
                }
            }

        # Mostrar el JSON en formato legible
        try:
            raw_str = raw_body.decode("utf-8")
            _logger.info(raw_str)
            _logger.info("*" * 80)
        except UnicodeDecodeError:
            _logger.warning(
                "⚠️  [PDA ORDER] No se puede decodificar JSON (encoding inválido)"
            )
            _logger.info("*" * 80)

        if len(raw_body) > _MAX_PAYLOAD_BYTES:
            _logger.error(
                f"❌ [PDA ORDER] Payload demasiado grande: "
                f"{len(raw_body) // 1024} KB (máximo: {_MAX_PAYLOAD_BYTES // 1024} KB)"
            )
            max_kb = _MAX_PAYLOAD_BYTES // 1024
            return {
                "_error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": (
                        f"El payload supera el tamaño máximo permitido ({max_kb} KB)."
                    ),
                    "status": 413,
                }
            }

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _logger.error(f"❌ [PDA ORDER] JSON mal formado: {exc}")
            return {
                "_error": {
                    "code": "INVALID_JSON",
                    "message": f"JSON mal formado: {exc}",
                    "status": 400,
                }
            }
        if not isinstance(payload, dict):
            return {
                "_error": {
                    "code": "INVALID_JSON",
                    "message": "El payload debe ser un objeto JSON (dict).",
                    "status": 400,
                }
            }
        return payload

    @staticmethod
    def _validate_payload(payload):
        external_ref = payload.get("external_reference") or payload.get("uuid")
        if not external_ref or not str(external_ref).strip():
            return "Campo obligatorio ausente: 'external_reference' (o 'uuid')."

        lines = payload.get("lineas") if "lineas" in payload else payload.get("lines")
        if lines is None:
            return "Campo obligatorio ausente: 'lineas'."
        if not isinstance(lines, list) or not lines:
            return "El campo 'lineas' debe ser una lista no vacía."

        for idx, line in enumerate(lines, start=1):
            if not isinstance(line, dict):
                return f"La línea {idx} no es un objeto JSON válido."
            if not any(
                line.get(key)
                for key in ("product_id", "default_code", "barcode", "id_articulo")
            ):
                return (
                    f"Línea {idx}: debe indicar 'product_id', 'default_code', "
                    "'barcode' o 'id_articulo'."
                )
            qty = line.get("qty", line.get("unidades"))
            if qty is None:
                return f"Línea {idx}: campo obligatorio ausente 'qty'."
            try:
                float(qty)
            except (TypeError, ValueError):
                return f"Línea {idx}: 'qty' debe ser un número."

            price_unit = line.get("price_unit", line.get("precio"))
            if price_unit is not None:
                try:
                    float(price_unit)
                except (TypeError, ValueError):
                    return f"Línea {idx}: 'price_unit' debe ser un número."

            discount = line.get("discount", 0.0)
            try:
                discount_val = float(discount)
            except (TypeError, ValueError):
                return f"Línea {idx}: 'discount' debe ser un número."
            if discount_val < 0 or discount_val > 100:
                return f"Línea {idx}: 'discount' debe estar entre 0 y 100."

        payments = payload.get("payments", [])
        if payments and not isinstance(payments, list):
            return "El campo 'payments' debe ser una lista."
        amount_paid = payload.get("amount_paid")
        if amount_paid is not None:
            try:
                float(amount_paid)
            except (TypeError, ValueError):
                return "El campo 'amount_paid' debe ser un número."
        return None

    def _create_pos_order_from_payload(
        self, payload, token_rec, pos_config, open_session, order_uuid, external_ref
    ):
        _logger.info("🔄 [PDA ORDER] Resolviendo datos del pedido...")


        partner = self._resolve_partner(payload, pos_config)
        pricelist = self._resolve_pricelist(
            payload=payload,
            partner=partner,
            pos_config=pos_config,
            company=open_session.company_id,
        )
        date_order = payload.get("date_order") or fields.Datetime.now()
        to_invoice = self._coerce_bool(payload.get("to_invoice"), default=True)
        lines_payload = (
            payload.get("lineas") if "lineas" in payload else payload.get("lines")
        )

        _logger.info(f"   - Partner: {partner.name if partner else 'Sin cliente'}")
        _logger.info(f"   - to_invoice: {to_invoice}")
        _logger.info(f"   - date_order: {date_order}")

        line_commands = []
        total_tax = 0.0
        total_incl = 0.0
        _logger.info(f"📋 [PDA ORDER] Procesando {len(lines_payload)} línea(s)...")
        for idx, line_payload in enumerate(lines_payload, start=1):
            line_vals, tax_amount, total_amount = self._prepare_order_line_vals(
                line_payload=line_payload,
                partner=partner,
                pos_config=pos_config,
                company=open_session.company_id,
            )
            line_commands.append((0, 0, line_vals))
            total_tax += tax_amount
            total_incl += total_amount
            _logger.info(
                f"     ✓ Línea {idx}: {line_vals['name'][:50]} "
                f"(qty={line_vals['qty']}, total={total_amount:.2f})"
            )

        _logger.info("💾 [PDA ORDER] Guardando pedido en BD...")

        # _logger.debug(
        #     "[PDA ORDER] JSON recibido desde la PDA:\n%s",
        #     json.dumps(payload, indent=2, default=str, ensure_ascii=False),
        # )

        # ========== IMPRIMIR JSON RECIBIDO DESDE LA PDA ==========
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("prueba")
        print("")
        print("📥 [PDA ORDER] JSON RECIBIDO DESDE LA PDA:")
        print("")
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        print("")
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("")

        # Preparar el diccionario de creación del pedido
        order_dict = {
            "name": "/",
            "uuid": order_uuid,
            "session_id": open_session.id,
            "user_id": token_rec.sale_user_id.id,
            "company_id": open_session.company_id.id,
            "partner_id": partner.id if partner else False,
            "date_order": date_order,
            "to_invoice": to_invoice,
            "internal_note": f"PDA external_reference: {external_ref}",
            "pda_external_reference": external_ref or False,
            "pricelist_id": pricelist.id if pricelist else False,
            "fiscal_position_id": partner.property_account_position_id.id
            if partner
            else False,
            "lines": line_commands,
            "amount_tax": total_tax,
            "amount_total": total_incl,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        }

        # Imprimir JSON usado para crear el pedido
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("")
        print("🔧 [PDA ORDER] JSON USADO PARA CREAR EL PEDIDO EN ODOO:")
        print("")
        # Log del JSON usado para crear el pedido
        order_dict_display = order_dict.copy()
        order_dict_display["lines"] = f"[{len(line_commands)} líneas de pedido]"
        # _logger.debug(
        #     "[PDA ORDER] JSON usado para crear el pedido en Odoo:\n%s",
        #     json.dumps(order_dict_display, indent=2, default=str, ensure_ascii=False),
        # )
        print(json.dumps(order_dict_display, indent=2, default=str, ensure_ascii=False))
        print("")
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)
        print("*" * 80)

        order_model = (
            request.env["pos.order"].sudo().with_company(open_session.company_id)
        )
        order = order_model.create(order_dict)

        # Marca el pedido como "factura simplificada" española para que el
        # número de la factura aparezca en el campo estándar
        # ``l10n_es_simplified_invoice_number`` (columna "Número de factura
        # simplificada"). El campo es computado a partir de account_move.name
        # cuando este flag está activo.
        if "is_l10n_es_simplified_invoice" in order._fields:
            order.is_l10n_es_simplified_invoice = True

        # `amount_total`/`amount_tax` en `order_dict` son una suma manual
        # línea a línea calculada en este controlador (vía
        # ``_prepare_order_line_vals``). Esa suma puede no coincidir con
        # el total oficial que calcula Odoo en ``_compute_prices()``
        # (que usa ``AccountTax._get_tax_totals_summary`` y respeta el
        # método de redondeo configurado en la compañía: por línea o
        # global, más el posible "cash rounding" del TPV). Si no
        # recalculamos AQUÍ, antes de resolver el importe del pago
        # automático, ``_resolve_auto_payment_amount`` usaría un
        # ``amount_total`` desactualizado y el pago generado podría
        # quedarse corto frente al total real, hacienda fallar después
        # la validación "los pagos deben cubrir el total" aunque se
        # pretendía pagar el 100 %.
        order._compute_prices()

        mark_as_paid = self._coerce_bool(payload.get("mark_as_paid"), default=True)
        payments = list(payload.get("payments", []))
        if mark_as_paid and not payments:
            payments = [
                self._build_default_payment_payload(
                    order=order,
                    open_session=open_session,
                    payment_date=date_order,
                    payload=payload,
                )
            ]

        _logger.info(f"💳 [PDA ORDER] Procesando {len(payments)} pago(s)...")
        is_refund = (
            float_compare(
                order.amount_total,
                0.0,
                precision_rounding=order.currency_id.rounding,
            )
            < 0
        )
        if is_refund:
            _logger.info(
                "↩️  [PDA ORDER] Importe negativo detectado: se procesa como "
                "DEVOLUCIÓN (total=%.2f).",
                order.amount_total,
            )
        for _idx, payment in enumerate(payments, start=1):
            payment_method = self._resolve_payment_method(payment, open_session)
            amount = float(payment.get("amount", 0.0) or 0.0)
            # En una venta el importe es positivo; en una devolución es
            # negativo. Solo se rechaza el importe cero (no aporta nada).
            if float_is_zero(amount, precision_rounding=order.currency_id.rounding):
                raise ValidationError(
                    request.env._("El importe del pago no puede ser cero.")
                )
            order.add_payment(
                {
                    "pos_order_id": order.id,
                    "payment_method_id": payment_method.id,
                    "amount": amount,
                    "payment_date": payment.get("payment_date")
                    or fields.Datetime.now(),
                    "uuid": str(payment.get("uuid") or uuid4()),
                }
            )
        # Recalcula amount_paid/amount_return (y confirma amount_total)
        # ahora que los pagos ya están creados.
        order._compute_prices()

        if mark_as_paid:
            rounding = order.currency_id.rounding
            balance = float_compare(
                order.amount_paid, order.amount_total, precision_rounding=rounding
            )
            # Venta (total >= 0): los pagos deben cubrir el total (>=).
            # Devolución (total < 0): el importe devuelto (negativo) debe
            # cubrir el total negativo (<=).
            covered = balance >= 0 if order.amount_total >= 0 else balance <= 0
            if not covered:
                raise ValidationError(
                    request.env._(
                        "Para cerrar el pedido como pagado, los pagos deben "
                        "cubrir el total."
                    )
                )
            order.action_pos_order_paid()

            # Cierre estándar del pedido: albarán de entrega + factura
            # simplificada (account.move), igual que una venta POS normal.
            order.matriz_almonte_generate_picking_and_invoice()

        return order


    @staticmethod
    def _resolve_pricelist(payload, partner, pos_config, company):
        """Determina la tarifa de precios del pedido POS.

        ``pos.order`` exige una tarifa de precios (``pricelist_id``). En
        algunos TPV ``pos_config.pricelist_id`` puede estar vacío (por
        ejemplo si el TPV no usa tarifas), lo que provocaba el error
        "El pedido «/» debe tener una tarifa de precios.". Para evitarlo
        se resuelve una tarifa por orden de preferencia:
        1. ``pricelist_id`` recibido explícitamente en el payload.
        2. Tarifa configurada en el TPV.
        3. Tarifa del cliente (``property_product_pricelist``).
        4. Cualquier tarifa de la compañía (o sin compañía) como último
           recurso.
        """
        Pricelist = request.env["product.pricelist"].sudo()

        payload_pricelist_id = payload.get("pricelist_id")
        if payload_pricelist_id:
            pricelist = Pricelist.browse(int(payload_pricelist_id))
            if not pricelist.exists():
                raise ValidationError(
                    request.env._(
                        "La tarifa de precios %s no existe.", payload_pricelist_id
                    )
                )
            return pricelist

        if pos_config.pricelist_id:
            return pos_config.pricelist_id.sudo()

        if partner and partner.property_product_pricelist:
            return partner.property_product_pricelist.sudo()

        fallback = Pricelist.search(
            [
                "|",
                ("company_id", "=", company.id),
                ("company_id", "=", False),
            ],
            limit=1,
        )
        if not fallback:
            raise ValidationError(
                request.env._(
                    "No se ha podido determinar una tarifa de precios para el "
                    "pedido. Configura una tarifa en el TPV o en el cliente."
                )
            )
        return fallback

    @staticmethod
    def _resolve_partner(payload, pos_config):
        # OJO: esta ruta se publica con auth="none", por lo que
        # ``request.env`` no lleva usuario asociado (``request.env.uid``
        # es ``None`` y ``request.env.su`` es ``False``; ver
        # ``ir_http._auth_method_none``). Si se devuelve un recordset que
        # no esté forzado a ``sudo()``, el mero acceso a un campo
        # (p.ej. ``partner.name``) puede disparar la comprobación de ACL
        # a nivel de campo (``_has_field_access``), que termina llamando
        # a ``self.env.user.has_groups(...)``. Como no hay usuario,
        # ``env.user`` es un recordset vacío y ``has_groups`` explota con
        # ``ValueError: Expected singleton: res.users()``.
        # Por eso TODOS los caminos de resolución del partner deben
        # devolver el recordset en modo sudo().
        Partner = request.env["res.partner"].sudo()
        partner_id = payload.get("partner_id")
        if partner_id:
            partner = Partner.browse(int(partner_id))
            if not partner.exists():
                raise ValidationError(
                    request.env._("El partner_id %s no existe.", partner_id)
                )
            return partner
        # Fallback 1: cliente por defecto del TPV (factura simplificada anónima).
        if "default_partner_id" in pos_config._fields and pos_config.default_partner_id:
            return pos_config.default_partner_id.sudo()
        # Fallback 2: consumidor final del módulo, para poder emitir la factura.
        consumer = request.env.ref(
            "matriz_almonte_pda_sale_import.partner_consumidor_final",
            raise_if_not_found=False,
        )
        return consumer.sudo() if consumer else Partner

    @staticmethod
    def _resolve_product(line_payload):
        Product = request.env["product.product"].sudo()
        product = False
        if line_payload.get("product_id"):
            product = Product.browse(int(line_payload["product_id"]))
            product = product if product.exists() else False
        if not product and line_payload.get("default_code"):
            product = Product.search(
                [("default_code", "=", str(line_payload["default_code"]).strip())],
                limit=1,
            )
        if not product and line_payload.get("barcode"):
            product = Product.search(
                [("barcode", "=", str(line_payload["barcode"]).strip())], limit=1
            )
        if not product and line_payload.get("id_articulo"):
            product = Product.search(
                [("default_code", "=", str(line_payload["id_articulo"]).strip())],
                limit=1,
            )
        if not product:
            raise ValidationError(
                request.env._("No se ha encontrado el producto de una de las líneas.")
            )
        if not product.active or not product.sale_ok:
            raise ValidationError(
                request.env._(
                    "El producto '%s' no está activo o no es vendible.",
                    product.display_name,
                )
            )
        return product

    def _prepare_order_line_vals(self, line_payload, partner, pos_config, company):
        product = self._resolve_product(line_payload)
        qty = float(line_payload.get("qty", line_payload.get("unidades")))
        raw_price = line_payload.get("price_unit", line_payload.get("precio"))
        # La PDA envía SIEMPRE ``price_unit`` con IMPUESTOS INCLUIDOS.
        price_unit_incl = float(raw_price or 0.0)
        # La PDA puede enviar ``price_unit`` a 0 (por ejemplo en las
        # devoluciones): en ese caso se toma el precio de catálogo del
        # producto para que el total de la línea (y por tanto el importe
        # a devolver) sea correcto en vez de quedar a cero.
        if not price_unit_incl:
            price_unit_incl = float(product.lst_price or 0.0)
        discount = float(line_payload.get("discount", 0.0) or 0.0)

        taxes = product.taxes_id.filtered_domain(
            request.env["account.tax"]._check_company_domain(company)
        )
        fiscal_position = (
            partner.property_account_position_id
            if partner
            else request.env["account.fiscal.position"]
        )
        taxes_after_fpos = fiscal_position.map_tax(taxes) if fiscal_position else taxes
        unit_price_after_discount = price_unit_incl * (1 - discount / 100.0)

        # La PDA envía SIEMPRE precios finales, con impuestos incluidos
        # (tanto ``price_unit`` de cada línea como ``amount_total``/
        # ``amount_paid`` de cabecera). Con ``force_price_include=True``
        # (que Odoo mapea a ``special_mode='total_included'``) el motor de
        # impuestos interpreta ``unit_price_after_discount`` como el TOTAL
        # ya con impuestos y calcula la base hacia atrás, sin depender de
        # cómo esté configurado el impuesto.
        tax_data = taxes_after_fpos.with_context(
            force_price_include=True
        ).compute_all(
            unit_price_after_discount,
            currency=pos_config.currency_id,
            quantity=qty,
            product=product,
            partner=partner if partner else False,
        )
        total_excluded = tax_data["total_excluded"]
        total_included = tax_data["total_included"]

        # ``pos.order._compute_prices()`` recalcula el total del pedido a
        # partir de ``line.price_unit`` INTERPRETÁNDOLO según el flag
        # ``price_include`` del impuesto:
        #   - Impuesto "incluido en el precio" (típico IVA español en TPV):
        #     ``price_unit`` se trata como precio CON impuestos.
        #   - Impuesto añadido por encima: ``price_unit`` se trata como
        #     precio SIN impuestos.
        # Por eso guardamos ``price_unit`` en la convención correcta según
        # el impuesto; si no, base/impuestos/total salían mal.
        price_included = bool(taxes_after_fpos) and all(
            t.price_include for t in taxes_after_fpos
        )
        if qty and discount != 100:
            divisor = qty * (1 - discount / 100.0)
            if price_included:
                line_price_unit = total_included / divisor
            else:
                line_price_unit = total_excluded / divisor
        else:
            line_price_unit = price_unit_incl if price_included else 0.0

        return (
            {
                "name": str(line_payload.get("description") or product.display_name),
                "product_id": product.id,
                "qty": qty,
                "price_unit": line_price_unit,
                "discount": discount,
                "tax_ids": [(6, 0, taxes.ids)],
                "price_subtotal": total_excluded,
                "price_subtotal_incl": total_included,
                "uuid": str(line_payload.get("uuid") or uuid4()),
            },
            total_included - total_excluded,
            total_included,
        )

    @staticmethod
    def _coerce_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "t", "yes", "y", "si", "sí", "on"}:
                return True
            if normalized in {"0", "false", "f", "no", "n", "off", ""}:
                return False
        return default

    @classmethod
    def _is_print_requested(cls, payload):
        if "is_printer" in payload:
            return cls._coerce_bool(payload.get("is_printer"))
        return cls._coerce_bool(payload.get("imprimir", False))

    def _print_factura_simplificada(self, order, pos_config):
        """Imprime FÍSICAMENTE la factura simplificada del pedido.

        Reproduce EXACTAMENTE lo que ocurre cuando, desde el formulario del
        pedido, se pulsa el botón "Factura simplificada 80mm"
        (``action_print_factura_simplificada`` del módulo
        ``pos_conventional``):

          1. Se invoca esa misma acción para obtener el informe QWeb de la
             ``account.move`` asociada.
          2. Se renderiza el PDF de ese informe (idéntico al que vería el
             usuario) y se adjunta al pedido para dejar constancia y poder
             reimprimirlo/reenviarlo por email más tarde.
          3. Se convierte ese PDF en una imagen rasterizada ESC/POS y se
             envía DIRECTAMENTE a la impresora térmica del TPV (por TCP
             directo o por el bridge local), tal cual haría el navegador al
             mandar el documento a la impresora predeterminada.

        Devuelve un dict con ``printed`` = ``True`` solo si el ticket llegó
        físicamente a la impresora. Si ``printed`` es ``False`` (o se
        devuelve ``None`` porque la acción no aplica), ``_dispatch_order_print``
        continúa con el ticket de texto plano como plan B.
        """
        try:
            # La factura se acaba de generar dentro de esta misma petición.
            # Refrescamos/recuperamos explícitamente el enlace porque el valor
            # vacío de ``account_move`` puede permanecer cacheado en el
            # recordset, haciendo que el método del botón devuelva ``None``.
            if hasattr(order, "matriz_almonte_ensure_account_move"):
                move = order.matriz_almonte_ensure_account_move()
            else:
                order.flush_recordset()
                order.invalidate_recordset(["account_move"])
                move = order.account_move
            if not move:
                error = (
                    "El pedido está integrado pero no tiene una factura "
                    "simplificada enlazada (account_move vacío)."
                )
                _logger.error(
                    "[PDA ORDER] %s Pedido %s (id=%s, estado=%s, "
                    "to_invoice=%s).",
                    error,
                    order.name,
                    order.id,
                    order.state,
                    order.to_invoice,
                )
                return {"printed": False, "print_error": error}

            _logger.info(
                "[PDA ORDER] Factura enlazada confirmada antes de imprimir: "
                "pedido=%s (id=%s), factura=%s (id=%s).",
                order.name,
                order.id,
                move.name,
                move.id,
            )
            # ====== LLAMADA A LA MISMA ACCIÓN QUE EL BOTÓN MANUAL ======
            action = order.action_print_factura_simplificada()
        except Exception as exc:  # noqa: BLE001 - la impresión no debe romper
            _logger.exception(
                "[PDA ORDER] Error llamando a action_print_factura_simplificada "
                "en el pedido %s: %s",
                order.name,
                exc,
            )
            return {"printed": False, "print_error": str(exc)}

        action_type = action.get("type") if action else None
        if action_type not in {"ir.actions.report", "ir.actions.client"}:
            _logger.warning(
                "[PDA ORDER] action_print_factura_simplificada no devolvió un "
                "informe ni una acción cliente imprimible para el pedido %s. "
                "Acción devuelta: %r; factura: %s.",
                order.name,
                action,
                move.display_name,
            )
            return None

        response = {
            "printed": False,
            "print_mode": "factura_simplificada",
            "invoice_id": move.id,
            "invoice_name": move.name,
            "print_action_type": action_type,
            "print_action_tag": action.get("tag") or False,
        }

        # En producción ``pos_conventional_qztray`` devuelve exactamente:
        #   {type: "ir.actions.client",
        #    tag: "pos_conventional_print_receipt_qztray_window", ...}
        # Esa es la acción directa y oficial que usa el botón manual para
        # imprimir mediante QZ Tray. No debe tratarse como informe ni
        # renderizarse en el servidor: se publica inmediatamente para que el
        # navegador ejecute su tag con ``action.doAction()``.
        if action_type == "ir.actions.client":
            _logger.info(
                "[PDA ORDER] Acción cliente de impresión directa detectada "
                "para el pedido %s: tag=%s, params=%s.",
                order.name,
                action.get("tag"),
                action.get("params"),
            )
            dispatch_result = self._send_qztray_print(order, pos_config)
            response.update(dispatch_result)
            if dispatch_result.get("printed"):
                response["print_mode"] = "factura_simplificada_qztray_client_action"
            return response

        report_name = action.get("report_name")
        # URL del informe HTML (mismo patrón que usa pos_conventional).
        report_url = f"/report/html/{report_name}/{move.id}"
        response.update({"report_name": report_name, "report_url": report_url})

        # Renderizamos el PDF real del informe (el mismo que genera el
        # botón) y lo adjuntamos al pedido (best-effort).
        pdf_bytes = None
        try:
            report = (
                order.env["ir.actions.report"]
                .sudo()
                ._get_report_from_name(report_name)
            )
            pdf_bytes, _content_type = report._render_qweb_pdf(report_name, move.ids)
            pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
            attachment = (
                order.env["ir.attachment"]
                .sudo()
                .create(
                    {
                        "name": f"Factura_simplificada_{move.name}.pdf".replace(
                            "/", "-"
                        ),
                        "type": "binary",
                        "datas": pdf_b64,
                        "res_model": "pos.order",
                        "res_id": order.id,
                        "mimetype": "application/pdf",
                    }
                )
            )
            response.update(
                {
                    "ticket_pdf_base64": pdf_b64,
                    "ticket_pdf_url": f"/web/content/{attachment.id}?download=true",
                }
            )
        except Exception as exc:  # noqa: BLE001 - el PDF es opcional
            _logger.warning(
                "[PDA ORDER] No se pudo renderizar el PDF de la factura "
                "simplificada del pedido %s: %s",
                order.name,
                exc,
            )
            response["print_error"] = f"PDF no generado: {exc}"

        if not pdf_bytes:
            return response

        # ====== VÍA 1: notificar al backend (navegador con sesión de Odoo
        # abierta) para que reproduzca el click manual del botón "Factura
        # simplificada 80mm". Es el método preferente porque no depende de
        # que nosotros sepamos el mecanismo exacto de impresión directa
        # configurado (QZ Tray, base_report_to_printer, IPP/CUPS...): deja
        # que Odoo lo resuelva exactamente igual que en el click manual. ==
        qztray_result = self._send_qztray_print(order, pos_config)
        last_error = qztray_result.get("print_error")
        if qztray_result.get("printed"):
            response.update(qztray_result)
            response["printed"] = True
            response["print_mode"] = "factura_simplificada_backend_action"
            _logger.info(
                "[PDA ORDER] Factura simplificada %s notificada al backend "
                "para impresión automática del pedido %s.",
                move.name,
                order.name,
            )
            return response

        # ====== VÍA 2 y 3: convertir el PDF real a imagen ESC/POS y
        # enviarla directamente a la impresora térmica del TPV (TCP o
        # bridge local). Solo aplica a impresoras alcanzables por red o
        # con el bridge instalado; no requiere navegador. ======
        try:
            width_dots = self._get_printer_width_dots(pos_config)
            raster_bytes = self._pdf_bytes_to_escpos_raster(
                pdf_bytes, width_dots=width_dots
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[PDA ORDER] No se pudo convertir a imagen ESC/POS la factura "
                "simplificada del pedido %s: %s",
                order.name,
                exc,
            )
            raster_bytes = None
            response["print_error"] = last_error or f"Conversión a ticket fallida: {exc}"

        if raster_bytes:
            doc_name = f"Factura_{move.name or order.name}"

            # 2) Impresora térmica con IP de red propia (TCP directo).
            printer_cfg = self._get_direct_printer_config(pos_config)
            if printer_cfg.get("host"):
                tcp_result = self._print_ticket_via_socket(
                    order, printer_cfg, raw_bytes=raster_bytes
                )
                if tcp_result.get("printed"):
                    response.update(tcp_result)
                    response["printed"] = True
                    response["print_mode"] = "factura_simplificada_ticket"
                    _logger.info(
                        "[PDA ORDER] Factura simplificada %s impresa "
                        "directamente (TCP) para el pedido %s.",
                        move.name,
                        order.name,
                    )
                    return response
                last_error = tcp_result.get("print_error")

            # 3) Bridge local HTTP (xtendoo_cash_drawer_windows_client).
            bridge_result = self._send_bridge_print(
                order,
                pos_config,
                raw_bytes=raster_bytes,
                doc_name=doc_name,
            )
            if bridge_result.get("printed"):
                response.update(bridge_result)
                response["printed"] = True
                response["print_mode"] = "factura_simplificada_ticket"
                _logger.info(
                    "[PDA ORDER] Factura simplificada %s impresa mediante "
                    "el bridge local para el pedido %s.",
                    move.name,
                    order.name,
                )
                return response

            response["print_error"] = (
                last_error
                or bridge_result.get("print_error")
                or response.get("print_error")
            )

        _logger.warning(
            "[PDA ORDER] No se pudo imprimir físicamente la factura "
            "simplificada del pedido %s; se deja disponible el PDF (%s).",
            order.name,
            response.get("print_error"),
        )
        return response

    def _send_qztray_print(self, order, pos_config):
        """Notifica por el BUS de Odoo que hay que reproducir el click
        manual del botón "Factura simplificada 80mm" para este pedido.

        No reimplementa la lógica de impresión directa (QZ Tray,
        ``base_report_to_printer`` u otro mecanismo instalado): en su
        lugar, publica el ``order_id`` en el mismo canal en tiempo real
        que usa internamente ``point_of_sale``
        (``pos_config.access_token``, vía ``pos.bus.mixin``). El servicio
        JS ``pda_auto_print_service`` (cargado en el backend general,
        ``web.assets_backend`` — el mismo contexto donde vive el botón
        manual) recibe la notificación, llama por RPC a
        ``action_print_factura_simplificada`` (la MISMA acción que el
        botón) y ejecuta el resultado con el servicio de acciones del
        backend (``action.doAction()``), disparando exactamente el mismo
        mecanismo de impresión directa que el click manual, sea cual sea
        el módulo que lo provea.

        Al ser una notificación en tiempo real (fire-and-forget), no hay
        confirmación síncrona; se marca ``printed: True`` de forma
        optimista y ``print_delivery: "async_backend_action"``. Requiere
        que haya una sesión de Odoo (backend) abierta en un navegador de
        la tienda; el resultado real se confirma después vía
        ``pda_print_ack_rpc`` (campo "Estado impresión PDA" del pedido).
        """
        # Marcamos "pendiente de confirmación" ANTES de publicar en el
        # bus. Si tras la impresión nadie lo cambia a success/error, es la
        # prueba definitiva de que ningún navegador con sesión de Odoo
        # abierta llegó a recibir la notificación.
        if "pda_print_ack_state" in order._fields:
            order.sudo().write(
                {
                    "pda_print_mode": "backend_action_bus",
                    "pda_print_ack_state": "pending",
                    "pda_print_ack_message": False,
                    "pda_print_ack_date": False,
                }
            )
        try:
            pos_config._notify(
                "PDA_PRINT_TICKET",
                {
                    "order_id": order.id,
                    "order_name": order.name,
                },
            )
        except Exception as exc:  # noqa: BLE001 - la impresión no debe romper
            _logger.exception(
                "[PDA ORDER] Error publicando notificación de impresión en "
                "el bus para el pedido %s: %s",
                order.name,
                exc,
            )
            if "pda_print_ack_state" in order._fields:
                order.sudo().write(
                    {
                        "pda_print_ack_state": "error",
                        "pda_print_ack_message": str(exc)[:250],
                    }
                )
            return {"printed": False, "print_error": str(exc)}

        _logger.info(
            "[PDA ORDER] Notificación de impresión publicada en el bus "
            "(canal %s) para el pedido %s.",
            pos_config.access_token,
            order.name,
        )
        return {
            "printed": True,
            "print_mode": "backend_action_bus",
            "print_delivery": "async_backend_action",
        }


    @staticmethod
    def _get_printer_width_dots(pos_config):
        pos_fields = pos_config._fields
        width = (
            pos_config.pda_ticket_printer_width_dots
            if "pda_ticket_printer_width_dots" in pos_fields
            else 0
        )
        return int(width) or _DEFAULT_PRINTER_WIDTH_DOTS

    def _pdf_bytes_to_escpos_raster(
        self, pdf_bytes, width_dots=_DEFAULT_PRINTER_WIDTH_DOTS
    ):
        """Convierte un PDF (bytes) en comandos ESC/POS de imagen rasterizada.

        Usa ``pdftoppm`` (poppler-utils) para renderizar el PDF a PNG y
        Pillow para convertir a blanco/negro (1 bit) y generar los bytes
        del comando ``GS v 0`` (imprimir imagen rasterizada), soportado por
        prácticamente cualquier impresora térmica ESC/POS.

        El resultado es visualmente idéntico al PDF/HTML que genera
        ``action_print_factura_simplificada`` (el mismo botón manual):
        NIF, QR, VERI*FACTU/TicketBAI, totales, etc. tal cual aparecen en
        el informe oficial.
        """
        if Image is None:
            raise RuntimeError("Pillow (PIL) no está disponible en el servidor.")

        # Ancho múltiplo de 8 para que el empaquetado en bytes sea exacto.
        width_dots = max(8, (int(width_dots) // 8) * 8)

        with tempfile.TemporaryDirectory(prefix="pda_ticket_") as tmpdir:
            tmp_path = Path(tmpdir)
            pdf_path = tmp_path / "ticket.pdf"
            pdf_path.write_bytes(pdf_bytes)

            prefix = tmp_path / "page"
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    str(_PDF_RASTER_DPI),
                    str(pdf_path),
                    str(prefix),
                ],
                check=True,
                timeout=30,
                capture_output=True,
            )

            page_files = sorted(tmp_path.glob("page*.png"))
            if not page_files:
                raise RuntimeError("pdftoppm no generó ninguna página del PDF.")

            pages = [Image.open(str(p)).convert("L") for p in page_files]

            # Concatenar todas las páginas verticalmente (un único ticket).
            total_height = sum(p.height for p in pages)
            max_width = max(p.width for p in pages)
            combined = Image.new("L", (max_width, total_height), color=255)
            y_offset = 0
            for page in pages:
                combined.paste(page, (0, y_offset))
                y_offset += page.height

            if combined.width != width_dots:
                ratio = width_dots / combined.width
                new_height = max(1, int(combined.height * ratio))
                combined = combined.resize(
                    (width_dots, new_height), Image.LANCZOS
                )

            bw_image = combined.convert("1", dither=Image.FLOYDSTEINBERG)
            return self._image_to_escpos_bytes(bw_image)

    @staticmethod
    def _image_to_escpos_bytes(bw_image, chunk_lines=_ESCPOS_RASTER_CHUNK_LINES):
        """Genera los bytes ESC/POS (comando ``GS v 0``) de una imagen 1-bit.

        Se trocea en bloques de ``chunk_lines`` líneas para maximizar la
        compatibilidad con impresoras térmicas que limitan el tamaño del
        buffer de imagen por comando.
        """
        width, height = bw_image.size
        width_bytes = width // 8  # width ya es múltiplo de 8

        init = b"\x1b\x40"  # ESC @ : inicializar impresora
        out = bytearray(init)

        for start in range(0, height, chunk_lines):
            end = min(start + chunk_lines, height)
            chunk = bw_image.crop((0, start, width, end))
            # PIL en modo "1": bit=1 → blanco, bit=0 → negro. ESC/POS
            # (GS v 0) necesita justo lo contrario: bit=1 → imprime punto.
            raw = bytes(b ^ 0xFF for b in chunk.tobytes())
            chunk_height = end - start
            xL, xH = width_bytes & 0xFF, (width_bytes >> 8) & 0xFF
            yL, yH = chunk_height & 0xFF, (chunk_height >> 8) & 0xFF
            out += bytes([0x1D, 0x76, 0x30, 0x00, xL, xH, yL, yH])
            out += raw

        out += b"\n\n\n\n"
        out += b"\x1d\x56\x41\x10"  # GS V A 16 : corte parcial con avance
        return bytes(out)

    def _send_bridge_print(self, order, pos_config, raw_bytes=None, doc_name=None):
        """Envía bytes RAW ESC/POS al bridge local (``/print-raw``).

        Si no se indica ``raw_bytes`` explícitamente, construye el ticket
        de texto plano genérico (comportamiento histórico) a partir del
        pedido, para mantener compatibilidad como último recurso.
        """
        bridge_config = self._get_print_bridge_config(pos_config)
        if bridge_config.get("error"):
            return {"printed": False, "print_error": bridge_config["error"]}

        hex_bytes = (
            ",".join(f"{byte:02X}" for byte in raw_bytes)
            if raw_bytes is not None
            else self._build_ticket_hex_bytes(order)
        )

        payload = {
            "printer": bridge_config["printer_name"],
            "hex_bytes": hex_bytes,
            "doc_name": doc_name or f"PDA_POS_{order.name or order.id}",
        }
        headers = {}
        if bridge_config["api_key"]:
            headers["x-api-key"] = bridge_config["api_key"]

        last_error = "No se pudo imprimir el ticket."
        for candidate_url in self._resolve_bridge_urls(bridge_config["print_url"]):
            try:
                response = http_requests.post(
                    candidate_url,
                    json=payload,
                    headers=headers,
                    timeout=10,
                )
            except http_requests.exceptions.RequestException as exc:
                last_error = str(exc)
                _logger.warning(
                    "[PDA ORDER] Error enviando ticket a %s: %s",
                    candidate_url,
                    exc,
                )
                continue

            try:
                response_payload = response.json()
            except ValueError:
                response_payload = {}

            if response.ok and response_payload.get("ok", True):
                _logger.info(
                    "[PDA ORDER] Ticket enviado a impresora para pedido %s mediante %s",
                    order.name,
                    candidate_url,
                )
                return {
                    "printed": True,
                    "print_url": candidate_url,
                    "print_printer": bridge_config["printer_name"] or False,
                    "print_response": response_payload
                    or {"status_code": response.status_code},
                }

            last_error = response_payload.get("error") or (
                f"HTTP {response.status_code} al imprimir el ticket."
            )
            _logger.warning(
                "[PDA ORDER] El bridge devolvió error al imprimir pedido %s: %s",
                order.name,
                last_error,
            )

        return {
            "printed": False,
            "print_url": bridge_config["print_url"],
            "print_printer": bridge_config["printer_name"] or False,
            "print_error": last_error,
        }

    def _dispatch_order_print(self, order, pos_config):
        # 0) Impresión FÍSICA de la FACTURA SIMPLIFICADA oficial (80 mm)
        #    llamando a la MISMA acción que el botón manual del formulario
        #    (``action_print_factura_simplificada``, módulo pos_conventional).
        #    No comprobamos ``order.account_move`` aquí: puede estar vacío en
        #    la caché justo después de crear la factura. El método llamado se
        #    encarga de refrescar y recuperar el enlace de forma segura.
        extra_info = {}
        if hasattr(order, "action_print_factura_simplificada"):
            result = self._print_factura_simplificada(order, pos_config)
            if result is not None:
                if result.get("printed"):
                    return result
                # No se pudo imprimir físicamente el documento real (p.ej.
                # no hay impresora/bridge configurado, o falló la
                # conversión a imagen): conservamos la info (PDF adjunto,
                # URL, error) y probamos el ticket de texto plano como
                # plan B antes de rendirnos.
                extra_info = result

        # 1) Impresión DIRECTA en la impresora térmica de 80 mm por red
        #    (RAW/ESC-POS sobre TCP, puerto 9100 por defecto) cuando el
        #    TPV tiene configurada la IP de la impresora.
        printer = self._get_direct_printer_config(pos_config)
        direct_error = None
        if printer.get("host"):
            result = self._print_ticket_via_socket(order, printer)
            if result.get("printed"):
                return {**extra_info, **result}
            # Si la impresión directa falla se intenta el bridge local
            # (si existe) como plan B, conservando el error original.
            direct_error = result.get("print_error")

        # 2) Plan B: bridge local de impresión (ticket de texto genérico).
        bridge_result = self._send_bridge_print(order, pos_config)
        if bridge_result.get("printed"):
            return {**extra_info, **bridge_result}

        return {
            **extra_info,
            "printed": False,
            "print_error": (
                direct_error
                or bridge_result.get("print_error")
                or extra_info.get("print_error")
            ),
        }

    @staticmethod
    def _get_direct_printer_config(pos_config):
        """Devuelve la configuración de la impresora térmica directa del TPV."""
        pos_fields = pos_config._fields
        host = (
            (pos_config.pda_ticket_printer_host or "").strip()
            if "pda_ticket_printer_host" in pos_fields
            else ""
        )
        port = (
            pos_config.pda_ticket_printer_port
            if "pda_ticket_printer_port" in pos_fields
            else 0
        ) or 9100
        return {"host": host, "port": int(port)}

    def _print_ticket_via_socket(self, order, printer, raw_bytes=None):
        """Imprime el ticket enviando los bytes ESC/POS directamente por TCP.

        Es el método de impresión directo para impresoras térmicas de
        80 mm conectadas por red (RAW/JetDirect, normalmente puerto 9100).

        Si no se indica ``raw_bytes`` explícitamente (p.ej. la imagen ESC/POS
        de la factura simplificada real), se construye el ticket de texto
        plano genérico a partir del pedido, como hasta ahora.
        """
        host = printer["host"]
        port = printer.get("port") or 9100
        if raw_bytes is None:
            raw_bytes = self._build_ticket_bytes(order)
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.sendall(raw_bytes)
        except OSError as exc:
            _logger.warning(
                "[PDA ORDER] Error imprimiendo directamente en %s:%s para "
                "pedido %s: %s",
                host,
                port,
                order.name,
                exc,
            )
            return {
                "printed": False,
                "print_printer": f"{host}:{port}",
                "print_error": (
                    f"No se pudo imprimir directamente en {host}:{port}: {exc}"
                ),
            }

        _logger.info(
            "[PDA ORDER] Ticket impreso directamente en %s:%s para pedido %s",
            host,
            port,
            order.name,
        )
        return {
            "printed": True,
            "print_printer": f"{host}:{port}",
            "print_mode": "direct_tcp",
        }

    @staticmethod
    def _normalize_bridge_base_url(raw_url):
        if not raw_url:
            return ""
        parsed = urlsplit(raw_url.strip())
        path = parsed.path or ""
        for suffix in ("/open-drawer", "/print-raw", "/health"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
        return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))

    def _get_print_bridge_config(self, pos_config):
        pos_fields = pos_config._fields
        bridge_url = ""
        if "cash_drawer_effective_url" in pos_fields:
            bridge_url = pos_config.cash_drawer_effective_url or ""
        elif "cash_drawer_bridge_url" in pos_fields:
            bridge_url = pos_config.cash_drawer_bridge_url or ""
        elif "cash_drawer_open_url" in pos_fields:
            bridge_url = pos_config.cash_drawer_open_url or ""

        bridge_url = self._normalize_bridge_base_url(bridge_url)
        if not bridge_url:
            return {
                "error": (
                    "No hay bridge de impresión configurado en el TPV. "
                    "Configura la URL del bridge local en el POS."
                )
            }

        return {
            "print_url": f"{bridge_url}/print-raw",
            "printer_name": (
                pos_config.cash_drawer_printer_name
                if "cash_drawer_printer_name" in pos_fields
                else ""
            )
            or "",
            "api_key": (
                pos_config.cash_drawer_api_key
                if "cash_drawer_api_key" in pos_fields
                else ""
            )
            or "",
        }

    @staticmethod
    def _resolve_bridge_urls(url):
        try:
            from odoo.addons.xtendoo_cash_drawer.controllers.cash_drawer import (
                _resolve_url,
            )
        except ImportError:
            return [url]
        return _resolve_url(url)

    @staticmethod
    def _normalize_ticket_text(value):
        normalized = unicodedata.normalize("NFKD", value or "")
        return normalized.encode("ascii", "replace").decode("ascii")

    def _build_ticket_bytes(self, order):
        """Construye el flujo de bytes ESC/POS del ticket (80 mm térmica).

        Devuelve ``bytes`` listos para enviar tal cual a la impresora
        (impresión directa por socket) o para convertir a hexadecimal
        (bridge local).
        """
        width = 48  # 80 mm en Fuente A = 48 columnas.
        company_name = self._normalize_ticket_text(order.company_id.name or "")
        order_name = self._normalize_ticket_text(order.name or order.pos_reference or "")
        order_ref = self._normalize_ticket_text(order.pos_reference or "")
        date_order = (
            fields.Datetime.to_string(order.date_order) if order.date_order else ""
        )
        separator = "-" * width
        is_refund = order.amount_total < 0

        lines = [
            "\x1b@\x1ba\x01",  # init + centrado
            company_name,
        ]
        if is_refund:
            # Texto en negrita para resaltar que es una devolución.
            lines.append("\x1bE\x01DEVOLUCION\x1bE\x00")
        lines.extend(
            [
                "\x1ba\x00",  # alineado a la izquierda
                separator,
                f"Pedido: {order_name}",
            ]
        )
        if order.account_move:
            lines.append(
                f"Factura simpl.: "
                f"{self._normalize_ticket_text(order.account_move.name or '')}"
            )
        if order_ref and order_ref != order_name:
            lines.append(f"Ref: {order_ref}")
        if date_order:
            lines.append(f"Fecha: {date_order}")
        if order.partner_id:
            lines.append(
                f"Cliente: {self._normalize_ticket_text(order.partner_id.display_name)}"
            )
        lines.append(separator)

        amount_col = 12
        detail_col = width - amount_col
        for line in order.lines:
            product_name = self._normalize_ticket_text(
                line.full_product_name or line.product_id.display_name or line.name or ""
            )
            qty_text = f"{line.qty:g}"
            total_text = f"{line.price_subtotal_incl:.2f}"
            detail_text = f"{qty_text} x {line.price_unit:.2f}"
            lines.append(product_name[:width])
            lines.append(f"{detail_text[:detail_col]:<{detail_col}}{total_text:>{amount_col}}")

        lines.extend(
            [separator, f"{'TOTAL':<{detail_col}}{order.amount_total:>{amount_col}.2f}"]
        )

        for payment in order.payment_ids:
            payment_name = self._normalize_ticket_text(payment.payment_method_id.name or "")
            lines.append(
                f"{payment_name[:detail_col]:<{detail_col}}{payment.amount:>{amount_col}.2f}"
            )

        lines.extend(["", "", "\x1dV\x00"])  # avance de papel + corte
        return "\n".join(lines).encode("cp850", errors="replace")

    def _build_ticket_hex_bytes(self, order):
        """Versión hexadecimal del ticket para el bridge local (``/print-raw``)."""
        return ",".join(f"{byte:02X}" for byte in self._build_ticket_bytes(order))

    @staticmethod
    def _resolve_payment_method(payment_payload, open_session):
        method_id = payment_payload.get("payment_method_id")
        if not method_id:
            raise ValidationError(
                request.env._(
                    "Cada pago en 'payments' debe incluir 'payment_method_id'."
                )
            )
        payment_method = request.env["pos.payment.method"].sudo().browse(int(method_id))
        if not payment_method.exists():
            raise ValidationError(
                request.env._("El payment_method_id %s no existe.", method_id)
            )
        if payment_method not in open_session.config_id.payment_method_ids:
            raise ValidationError(
                request.env._(
                    "El método de pago '%s' no está permitido en este TPV.",
                    payment_method.name,
                )
            )
        return payment_method

    @staticmethod
    def _build_default_payment_payload(order, open_session, payment_date, payload):
        payment_method = (
            MatrizAlmontePdaPosOrderController._resolve_payload_payment_method(
                payload=payload,
                open_session=open_session,
            )
            or MatrizAlmontePdaPosOrderController._default_payment_method(open_session)
        )
        return {
            "payment_method_id": payment_method.id,
            "amount": MatrizAlmontePdaPosOrderController._resolve_auto_payment_amount(
                payload=payload,
                order=order,
            ),
            "payment_date": payment_date or fields.Datetime.now(),
            "uuid": str(uuid4()),
        }

    @staticmethod
    def _default_payment_method(open_session):
        payment_methods = open_session.config_id.payment_method_ids
        if not payment_methods:
            raise ValidationError(
                request.env._(
                    "El TPV no tiene métodos de pago configurados para registrar "
                    "el cobro automático del pedido."
                )
            )

        if "is_cash_count" in payment_methods._fields:
            cash_methods = payment_methods.filtered("is_cash_count")
            if cash_methods:
                return cash_methods[0]

        return payment_methods[0]

    @staticmethod
    def _resolve_payload_payment_method(payload, open_session):
        # Orden de preferencia para determinar el método de pago del
        # cobro automático:
        #   1. ``payment_method_id`` explícito (id numérico de Odoo).
        #   2. ``fpago`` que envía la PDA: "00" = efectivo, "TR" = tarjeta.
        #   3. ``payment_type`` que envía la PDA: "cash" o "card".
        selector = payload.get("payment_method_id")
        if selector in (None, ""):
            selector = payload.get("fpago")
        if selector in (None, ""):
            selector = payload.get("payment_type")
        if selector in (None, ""):
            return False
        return MatrizAlmontePdaPosOrderController._resolve_payment_selector(
            selector=selector,
            open_session=open_session,
        )

    @staticmethod
    def _resolve_auto_payment_amount(payload, order):
        # El cobro automático salda el pedido por su TOTAL real calculado
        # por Odoo (``order.amount_total``). No se usa el ``amount_paid``
        # del payload porque puede diferir del total calculado (por el
        # redondeo de impuestos o porque la PDA envía ``price_unit`` = 0),
        # lo que provocaría el error "el pedido no está totalmente pagado".
        return order.amount_total

    @staticmethod
    def _resolve_payment_selector(selector, open_session):
        payment_methods = open_session.config_id.payment_method_ids
        if not payment_methods:
            raise ValidationError(
                request.env._(
                    "El TPV no tiene métodos de pago configurados para registrar pagos."
                )
            )

        normalized = str(selector).strip().lower()
        if normalized.isdigit() and int(normalized) > 0:
            method_by_id = payment_methods.filtered(lambda m: m.id == int(normalized))
            if method_by_id:
                return method_by_id[0]

        # Efectivo: "00"/"0"/"01"/"1" (códigos PDA) o "cash"/"efectivo".
        if normalized in {"00", "0", "01", "1", "cash", "efectivo"}:
            cash_method = MatrizAlmontePdaPosOrderController._default_payment_method(
                open_session
            )
            return cash_method

        # Tarjeta: "tr"/"02"/"2" (códigos PDA) o "card"/"tarjeta".
        if normalized in {"tr", "02", "2", "card", "tarjeta"}:
            if "is_cash_count" in payment_methods._fields:
                non_cash_methods = payment_methods.filtered(lambda m: not m.is_cash_count)
                if non_cash_methods:
                    return non_cash_methods[0]
            if len(payment_methods) > 1:
                return payment_methods[1]
            return payment_methods[0]

        raise ValidationError(
            request.env._(
                "No se pudo resolver el método de pago '%s' para este TPV.", selector
            )
        )
