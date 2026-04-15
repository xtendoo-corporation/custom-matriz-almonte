# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Modelo de tokens de acceso para dispositivos externos (PDAs).

Decisión de diseño — almacenamiento del token
----------------------------------------------
Se almacena el token en **texto plano** (campo ``token``) por las siguientes
razones operativas:

* El token es un secreto compartido generado en Odoo y entregado manualmente
  al operador que lo configura en la PDA.  No hay contraseña que el usuario
  «elija»: la genera el sistema con ``secrets.token_urlsafe(32)``.
* Al ser generado por Odoo y de alta entropía (256 bits), el riesgo de ataque
  de diccionario es inexistente.
* Almacenarlo hasheado impediría mostrarlo al administrador cuando lo necesite
  re-entregar a un dispositivo.  La alternativa (mostrar solo al crear) añade
  complejidad UX innecesaria en esta fase.
* La tabla ``matriz_almonte_api_token`` **no** debe ser accesible por usuarios
  no administradores: los ACL lo garantizan.

Si en el futuro se requiere mayor paranoia (acceso a BD comprometida), el
campo se puede migrar a hash SHA-256 sin cambiar el contrato de la API.
"""
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Longitud mínima del token para ser aceptado como válido
_TOKEN_MIN_LENGTH = 32


class MatrizAlmonteApiToken(models.Model):
    """Credencial de acceso para un dispositivo externo (PDA Android)."""

    _name = "matriz.almonte.api.token"
    _description = "Token de Acceso API - Matriz Almonte"
    _order = "name"
    _rec_name = "name"

    name = fields.Char(
        string="Nombre / Descripción",
        required=True,
        help="Nombre descriptivo del dispositivo o integración (p.ej. 'PDA-01 Almacén').",
    )
    token = fields.Char(
        string="Token",
        required=False,
        copy=False,
        help=(
            "Token de autenticación que debe incluir el dispositivo en la cabecera "
            "Authorization: Bearer <token>.  Generado automáticamente al crear el "
            "registro; puede regenerarse manualmente."
        ),
    )
    device_code = fields.Char(
        string="Código de Dispositivo",
        index=True,
        help="Identificador técnico del dispositivo (p.ej. 'PDA-ANDROID-01').",
    )
    active = fields.Boolean(
        string="Activo",
        default=True,
        help="Un token inactivo es rechazado por la API aunque sea válido.",
    )
    last_used_at = fields.Datetime(
        string="Último uso",
        readonly=True,
        copy=False,
        help="Fecha/hora de la última petición API autenticada con este token.",
    )
    notes = fields.Text(
        string="Notas",
        help="Información adicional sobre el dispositivo o la integración.",
    )
    import_count = fields.Integer(
        string="Importaciones",
        compute="_compute_import_count",
        help="Número total de importaciones recibidas con este token.",
    )

    # -------------------------------------------------------------------------
    # Compute
    # -------------------------------------------------------------------------

    def _compute_import_count(self):
        ImportModel = self.env["matriz.almonte.pda.sale.import"]
        for token in self:
            token.import_count = ImportModel.search_count(
                [("token_id", "=", token.id)]
            )

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("token"):
                vals["token"] = self._generate_token()
        return super().create(vals_list)

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------

    @api.constrains("token")
    def _check_token_length(self):
        for rec in self:
            if len(rec.token or "") < _TOKEN_MIN_LENGTH:
                raise ValidationError(
                    _(
                        "El token debe tener al menos %(min)d caracteres.",
                        min=_TOKEN_MIN_LENGTH,
                    )
                )

    _sql_constraints = [
        (
            "token_unique",
            "UNIQUE(token)",
            "Ya existe un token con ese valor. Usa 'Regenerar Token' para obtener uno nuevo.",
        ),
    ]

    # -------------------------------------------------------------------------
    # Actions / Buttons
    # -------------------------------------------------------------------------

    def action_regenerate_token(self):
        """Genera un nuevo token seguro para este dispositivo."""
        self.ensure_one()
        self.token = self._generate_token()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Token regenerado"),
                "message": _(
                    "Se ha generado un nuevo token para '%(name)s'. "
                    "Actualiza la configuración del dispositivo.",
                    name=self.name,
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_view_imports(self):
        """Abre las importaciones asociadas a este token."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Importaciones de %(name)s", name=self.name),
            "res_model": "matriz.almonte.pda.sale.import",
            "view_mode": "list,form",
            "domain": [("token_id", "=", self.id)],
            "context": {"default_token_id": self.id},
        }

    # -------------------------------------------------------------------------
    # Public helpers
    # -------------------------------------------------------------------------

    @api.model
    def authenticate(self, raw_token):
        """Busca y devuelve el token activo que coincide con ``raw_token``.

        :param raw_token: valor en texto plano enviado en la cabecera HTTP.
        :returns: recordset de un único token, o vacío si no es válido/activo.
        """
        if not raw_token or len(raw_token) < _TOKEN_MIN_LENGTH:
            return self.browse()
        token_rec = self.sudo().search(
            [("token", "=", raw_token), ("active", "=", True)], limit=1
        )
        if token_rec:
            # Actualizamos last_used_at sin pasar por el ORM completo para
            # no disparar recomputaciones innecesarias en cada petición.
            token_rec.sudo().write(
                {"last_used_at": fields.Datetime.now()}
            )
        return token_rec

    # -------------------------------------------------------------------------
    # Private
    # -------------------------------------------------------------------------

    @staticmethod
    def _generate_token():
        """Genera un token URL-safe de 256 bits (43 caracteres base64url)."""
        return secrets.token_urlsafe(32)
