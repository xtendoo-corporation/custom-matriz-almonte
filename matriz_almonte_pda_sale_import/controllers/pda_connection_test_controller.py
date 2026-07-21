# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Controlador HTTP para comprobar la conectividad de dispositivos externos."""
import json
import logging

from odoo import http
from odoo.http import request

from .pda_sale_import_controller import MatrizAlmontePdaSaleImportController

_logger = logging.getLogger(__name__)


def _json_response(data, status=200):
    """Devuelve una respuesta JSON homogénea para el test de conexión."""
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
    error_response = {
        "success": False,
        "code": code,
        "message": message,
        "token_id": None,
        "device_code": None,
        "pos_id": None,
        "pos_name": None,
        "sale_user_id": None,
        "sale_user_name": None,
        "default_user_id": None,
        "default_user_name": None,
    }
    error_response.update(extra)
    return _json_response(error_response, status=http_status)


def _remote_ip():
    """Devuelve la IP del cliente de forma segura."""
    return (
        request.httprequest.headers.get("X-Forwarded-For", "")
        or request.httprequest.remote_addr
        or "unknown"
    )


class MatrizAlmontePdaConnectionTestController(http.Controller):
    """Endpoint ligero para validar autenticación y conectividad externa."""

    _ROUTE = "/api/matriz_almonte/pda/connection/test"

    @http.route(
        _ROUTE,
        type="http",
        auth="none",
        methods=["GET", "POST"],
        csrf=False,
        save_session=False,
        cors="*",
    )
    def pda_connection_test(self, **kwargs):
        """Valida que el dispositivo puede alcanzar la API y autenticarse."""
        auth_header = request.httprequest.headers.get("Authorization", "")
        token_value = MatrizAlmontePdaSaleImportController._extract_bearer_token(
            auth_header
        )

        if not token_value:
            token_value = request.httprequest.headers.get("X-API-Token", "").strip()

        if not token_value:
            _logger.warning(
                "PDA API connection test: petición sin token desde %s",
                _remote_ip(),
            )
            return _error(
                "MISSING_TOKEN",
                "Se requiere autenticación. Incluye 'Authorization: Bearer <token>'.",
                http_status=401,
            )

        token_rec = request.env["matriz.almonte.api.token"].sudo().authenticate(
            token_value
        )
        if not token_rec:
            _logger.warning(
                "PDA API connection test: token inválido o inactivo desde %s",
                _remote_ip(),
            )
            return _error(
                "INVALID_TOKEN",
                "Token de autenticación inválido o inactivo.",
                http_status=401,
            )

        pos_config = token_rec.tienda_id
        pos_id = pos_config.id if pos_config else None
        sale_user_id = token_rec.sale_user_id.id if token_rec.sale_user_id else None
        sale_user_name = token_rec.sale_user_id.name if token_rec.sale_user_id else None
        
        # Usuario por defecto de la sesión actual (si hay sesión abierta)
        default_user_id = None
        default_user_name = None
        if pos_config:
            current_user = pos_config.current_user_id
            if current_user:
                default_user_id = current_user.id
                default_user_name = current_user.name

        return _json_response(
            {
                "success": True,
                "code": "CONNECTION_OK",
                "message": "Conexión validada correctamente.",
                "token_id": token_rec.id,
                "token_name": token_rec.name,
                "device_code": token_rec.device_code or "",
                "pos_id": pos_id,
                "pos_name": pos_config.name if pos_config else None,
                "sale_user_id": sale_user_id,
                "sale_user_name": sale_user_name,
                "default_user_id": default_user_id,
                "default_user_name": default_user_name,
            },
            status=200,
        )
