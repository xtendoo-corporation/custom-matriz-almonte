# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Controlador HTTP para consultar sesión POS y crear pedidos desde PDA."""
import json
import logging
from uuid import uuid4

from odoo import fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.tools import float_compare

from .pda_sale_import_controller import MatrizAlmontePdaSaleImportController

_logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 512 * 1024  # 512 KB


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
        latest_session = request.env["pos.session"].sudo().search(
            [("config_id", "=", pos_config.id)],
            order="id desc",
            limit=1,
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
        if not open_session:
            return _error(
                "SESSION_NOT_OPEN",
                (
                    "No hay ninguna sesión POS abierta para el Punto de Venta "
                    f"'{pos_config.name}'. Debe abrirse desde Odoo 18."
                ),
                http_status=409,
                pos_config_id=pos_config.id,
                pos_config_name=pos_config.name,
                required_fields=self._required_payload_fields(),
            )

        payload_or_error = self._read_json_payload()
        if isinstance(payload_or_error, dict) and payload_or_error.get("_error"):
            err = payload_or_error["_error"]
            return _error(
                err["code"],
                err["message"],
                http_status=err["status"],
                required_fields=self._required_payload_fields(),
            )
        payload = payload_or_error

        validation_error = self._validate_payload(payload)
        if validation_error:
            return _error(
                "VALIDATION_ERROR",
                validation_error,
                http_status=400,
                required_fields=self._required_payload_fields(),
            )

        external_ref = (
            str(payload.get("external_reference") or payload.get("uuid") or "").strip()
        )
        order_uuid = str(payload.get("uuid") or external_ref or uuid4())
        existing_order = request.env["pos.order"].sudo().search(
            [
                ("session_id.config_id", "=", pos_config.id),
                "|",
                ("uuid", "=", order_uuid),
                ("pos_reference", "=", external_ref),
            ],
            limit=1,
        )
        if existing_order:
            return _json_response(
                {
                    "success": True,
                    "code": "DUPLICATE",
                    "message": (
                        f"Ya existe un pedido POS con esa referencia (id={existing_order.id})."
                    ),
                    "order_id": existing_order.id,
                    "order_name": existing_order.name,
                    "external_reference": external_ref,
                    "session_id": existing_order.session_id.id,
                },
                status=200,
            )

        try:
            order = self._create_pos_order_from_payload(
                payload=payload,
                token_rec=token_rec,
                pos_config=pos_config,
                open_session=open_session,
                order_uuid=order_uuid,
                external_ref=external_ref,
            )
        except (ValidationError, UserError) as exc:
            return _error("VALIDATION_ERROR", str(exc), http_status=400)

        return _json_response(
            {
                "success": True,
                "code": "CREATED",
                "message": "Pedido POS creado correctamente.",
                "order_id": order.id,
                "order_name": order.name,
                "external_reference": external_ref,
                "session_id": order.session_id.id,
                "session_name": order.session_id.name,
                "session_state": order.session_id.state,
                "amount_total": order.amount_total,
                "amount_paid": order.amount_paid,
                "state": order.state,
            },
            status=200,
        )

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
                    "Se requiere autenticación. Incluye 'Authorization: Bearer <token>'.",
                    http_status=401,
                ),
            )

        token_rec = request.env["matriz.almonte.api.token"].sudo().authenticate(
            token_value
        )
        if not token_rec:
            _logger.warning("PDA POS API: token inválido o inactivo desde %s", _remote_ip())
            return (
                None,
                _error(
                    "INVALID_TOKEN",
                    "Token de autenticación inválido o inactivo.",
                    http_status=401,
                ),
            )
        return token_rec, None

    @staticmethod
    def _get_open_session(pos_config):
        return request.env["pos.session"].sudo().search(
            [("config_id", "=", pos_config.id), ("state", "=", "opened")],
            order="id desc",
            limit=1,
        )

    @staticmethod
    def _required_payload_fields():
        return {
            "header_required": ["external_reference", "lineas"],
            "line_required": ["product_id | default_code | barcode | id_articulo", "qty"],
            "line_optional": ["price_unit", "discount", "description", "uuid"],
            "payment_optional": ["payment_method_id", "amount", "payment_date"],
            "header_optional": ["uuid", "partner_id", "date_order", "to_invoice", "mark_as_paid", "payments"],
        }

    @staticmethod
    def _read_json_payload():
        raw_body = request.httprequest.get_data(as_text=False)
        if len(raw_body) > _MAX_PAYLOAD_BYTES:
            return {
                "_error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"El payload supera el tamaño máximo permitido ({_MAX_PAYLOAD_BYTES // 1024} KB).",
                    "status": 413,
                }
            }
        if not raw_body:
            return {
                "_error": {
                    "code": "EMPTY_BODY",
                    "message": "El cuerpo de la petición está vacío.",
                    "status": 400,
                }
            }
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
            if not any(line.get(key) for key in ("product_id", "default_code", "barcode", "id_articulo")):
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
        return None

    def _create_pos_order_from_payload(
        self, payload, token_rec, pos_config, open_session, order_uuid, external_ref
    ):
        partner = self._resolve_partner(payload)
        date_order = payload.get("date_order") or fields.Datetime.now()
        to_invoice = bool(payload.get("to_invoice", False))
        lines_payload = payload.get("lineas") if "lineas" in payload else payload.get("lines")

        line_commands = []
        total_tax = 0.0
        total_incl = 0.0
        for line_payload in lines_payload:
            line_vals, tax_amount, total_amount = self._prepare_order_line_vals(
                line_payload=line_payload,
                partner=partner,
                pos_config=pos_config,
                company=open_session.company_id,
            )
            line_commands.append((0, 0, line_vals))
            total_tax += tax_amount
            total_incl += total_amount

        order_model = request.env["pos.order"].sudo().with_company(open_session.company_id)
        order = order_model.create(
            {
                "name": "/",
                "uuid": order_uuid,
                "pos_reference": external_ref,
                "session_id": open_session.id,
                "user_id": token_rec.sale_user_id.id,
                "company_id": open_session.company_id.id,
                "partner_id": partner.id if partner else False,
                "date_order": date_order,
                "to_invoice": to_invoice,
                "pricelist_id": pos_config.pricelist_id.id,
                "fiscal_position_id": partner.property_account_position_id.id if partner else False,
                "lines": line_commands,
                "amount_tax": total_tax,
                "amount_total": total_incl,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )

        payments = payload.get("payments", [])
        payment_total = 0.0
        for payment in payments:
            payment_method = self._resolve_payment_method(payment, open_session)
            amount = float(payment.get("amount", 0.0) or 0.0)
            if amount <= 0:
                raise ValidationError("El importe del pago debe ser mayor que cero.")
            payment_total += amount
            order.add_payment(
                {
                    "pos_order_id": order.id,
                    "payment_method_id": payment_method.id,
                    "amount": amount,
                    "payment_date": payment.get("payment_date") or fields.Datetime.now(),
                    "uuid": str(payment.get("uuid") or uuid4()),
                }
            )

        order._compute_prices()

        mark_as_paid = bool(payload.get("mark_as_paid", False))
        if mark_as_paid:
            if float_compare(
                order.amount_paid,
                order.amount_total,
                precision_rounding=order.currency_id.rounding,
            ) < 0:
                raise ValidationError(
                    "Para cerrar el pedido como pagado, los pagos deben cubrir el total."
                )
            order.action_pos_order_paid()

        return order

    @staticmethod
    def _resolve_partner(payload):
        partner_id = payload.get("partner_id")
        if not partner_id:
            return request.env["res.partner"]
        partner = request.env["res.partner"].sudo().browse(int(partner_id))
        if not partner.exists():
            raise ValidationError(f"El partner_id {partner_id} no existe.")
        return partner

    @staticmethod
    def _resolve_product(line_payload):
        Product = request.env["product.product"].sudo()
        product = False
        if line_payload.get("product_id"):
            product = Product.browse(int(line_payload["product_id"]))
            product = product if product.exists() else False
        if not product and line_payload.get("default_code"):
            product = Product.search([("default_code", "=", str(line_payload["default_code"]).strip())], limit=1)
        if not product and line_payload.get("barcode"):
            product = Product.search([("barcode", "=", str(line_payload["barcode"]).strip())], limit=1)
        if not product and line_payload.get("id_articulo"):
            product = Product.search([("default_code", "=", str(line_payload["id_articulo"]).strip())], limit=1)
        if not product:
            raise ValidationError("No se ha encontrado el producto de una de las líneas.")
        if not product.active or not product.sale_ok:
            raise ValidationError(
                f"El producto '{product.display_name}' no está activo o no es vendible."
            )
        return product

    def _prepare_order_line_vals(self, line_payload, partner, pos_config, company):
        product = self._resolve_product(line_payload)
        qty = float(line_payload.get("qty", line_payload.get("unidades")))
        price_unit = float(
            line_payload.get("price_unit", line_payload.get("precio", product.lst_price))
            or 0.0
        )
        discount = float(line_payload.get("discount", 0.0) or 0.0)

        taxes = product.taxes_id.filtered_domain(
            request.env["account.tax"]._check_company_domain(company)
        )
        fiscal_position = partner.property_account_position_id if partner else request.env["account.fiscal.position"]
        taxes_after_fpos = fiscal_position.map_tax(taxes) if fiscal_position else taxes
        unit_price_after_discount = price_unit * (1 - discount / 100.0)
        tax_data = taxes_after_fpos.compute_all(
            unit_price_after_discount,
            currency=pos_config.currency_id,
            quantity=qty,
            product=product,
            partner=partner if partner else False,
        )

        return (
            {
                "name": str(line_payload.get("description") or product.display_name),
                "product_id": product.id,
                "qty": qty,
                "price_unit": price_unit,
                "discount": discount,
                "tax_ids": [(6, 0, taxes.ids)],
                "price_subtotal": tax_data["total_excluded"],
                "price_subtotal_incl": tax_data["total_included"],
                "uuid": str(line_payload.get("uuid") or uuid4()),
            },
            tax_data["total_included"] - tax_data["total_excluded"],
            tax_data["total_included"],
        )

    @staticmethod
    def _resolve_payment_method(payment_payload, open_session):
        method_id = payment_payload.get("payment_method_id")
        if not method_id:
            raise ValidationError(
                "Cada pago en 'payments' debe incluir 'payment_method_id'."
            )
        payment_method = request.env["pos.payment.method"].sudo().browse(int(method_id))
        if not payment_method.exists():
            raise ValidationError(
                f"El payment_method_id {method_id} no existe."
            )
        if payment_method not in open_session.config_id.payment_method_ids:
            raise ValidationError(
                f"El método de pago '{payment_method.name}' no está permitido en este TPV."
            )
        return payment_method
