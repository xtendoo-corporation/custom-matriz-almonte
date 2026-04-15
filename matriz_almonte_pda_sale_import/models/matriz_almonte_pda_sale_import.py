# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Modelo cabecera de importación PDA.

Representa cada operación de venta/preventa recibida desde una PDA Android.
Almacena tanto el payload JSON bruto como la información normalizada en campos
relacionales, para facilitar auditoría, re-procesamiento y futura integración
con ``sale.order`` o ``pos.order``.
"""
import hashlib
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MatrizAlmontePdaSaleImport(models.Model):
    """Cabecera de operación importada desde PDA."""

    _name = "matriz.almonte.pda.sale.import"
    _description = "Importación PDA - Operación de Venta"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "received_at desc, id desc"
    _rec_name = "name"

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    name = fields.Char(
        string="Referencia Interna",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("Nuevo"),
        help="Referencia interna generada automáticamente por la secuencia.",
    )
    external_reference = fields.Char(
        string="UUID Operación (PDA)",
        required=True,
        index=True,
        tracking=True,
        help="UUID único de la operación enviado por la PDA (campo 'uuid' del JSON).",
    )
    pda_id = fields.Integer(
        string="ID PDA",
        readonly=True,
        help="Identificador numérico interno de la PDA (campo 'id' del JSON).",
    )
    payload_hash = fields.Char(
        string="Hash Payload",
        index=True,
        readonly=True,
        copy=False,
        help="SHA-256 del payload JSON recibido. Usado para detectar duplicados exactos.",
    )

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    received_at = fields.Datetime(
        string="Recibido",
        required=True,
        readonly=True,
        default=fields.Datetime.now,
        help="Fecha/hora en que Odoo recibió la petición.",
    )
    operation_datetime = fields.Datetime(
        string="Fecha Operación",
        tracking=True,
        help="Fecha/hora de la operación según la PDA (campo 'fecha_hora' del JSON).",
    )

    # ------------------------------------------------------------------
    # Device / Auth
    # ------------------------------------------------------------------

    token_id = fields.Many2one(
        comodel_name="matriz.almonte.api.token",
        string="Token API",
        ondelete="restrict",
        readonly=True,
        index=True,
        tracking=True,
        help="Token de acceso con el que se autenticó la petición.",
    )
    device_code = fields.Char(
        string="Código Dispositivo",
        index=True,
        tracking=True,
        help="Código del dispositivo PDA tal como lo envió.",
    )

    # ------------------------------------------------------------------
    # Operation data
    # ------------------------------------------------------------------

    salesperson_code = fields.Char(
        string="Vendedor / Usuario",
        tracking=True,
        help="Código del usuario/vendedor enviado por la PDA (campo 'usuario' del JSON).",
    )
    customer_reference = fields.Char(
        string="Referencia Cliente",
        index=True,
        tracking=True,
        help="Referencia del cliente enviada por la PDA.",
    )
    payment_method = fields.Char(
        string="Forma de Pago",
        tracking=True,
        help="Código de forma de pago (campo 'fpago' del JSON).",
    )
    global_discount = fields.Float(
        string="Descuento Global (%)",
        digits=(5, 2),
        default=0.0,
        tracking=True,
        help="Descuento global aplicado a toda la operación (campo 'descuento' del JSON).",
    )
    print_ticket = fields.Boolean(
        string="Imprimir Ticket",
        default=False,
        tracking=True,
        help="Indica si la PDA solicitó imprimir ticket (campo 'imprimir' del JSON).",
    )
    total_amount = fields.Float(
        string="Importe Total",
        digits=(16, 2),
        compute="_compute_total_amount",
        store=True,
        readonly=False,
        tracking=True,
        help="Suma de los totales de todas las líneas (qty × precio × (1 - dto/100)).",
    )
    currency = fields.Char(
        string="Moneda",
        default="EUR",
        tracking=True,
    )
    notes = fields.Text(
        string="Observaciones",
    )

    # ------------------------------------------------------------------
    # Raw storage
    # ------------------------------------------------------------------

    payload_raw = fields.Text(
        string="Payload JSON Original",
        readonly=True,
        copy=False,
        help="Cuerpo JSON exacto recibido desde la PDA, sin ninguna modificación.",
    )
    processing_log = fields.Text(
        string="Log de Procesamiento",
        readonly=True,
        copy=False,
        help="Trazas del procesamiento interno. Útil para depuración.",
    )

    # ------------------------------------------------------------------
    # State & errors
    # ------------------------------------------------------------------

    state = fields.Selection(
        selection=[
            ("draft", "Borrador"),
            ("received", "Recibido"),
            ("processed", "Procesado"),
            ("duplicate", "Duplicado"),
            ("error", "Error"),
        ],
        string="Estado",
        default="draft",
        required=True,
        index=True,
        tracking=True,
    )
    error_message = fields.Text(
        string="Mensaje de Error",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------

    line_ids = fields.One2many(
        comodel_name="matriz.almonte.pda.sale.import.line",
        inverse_name="import_id",
        string="Líneas",
        copy=True,
    )
    line_count = fields.Integer(
        string="Nº Líneas",
        compute="_compute_line_count",
    )

    # ------------------------------------------------------------------
    # Future integration fields (preparados para fase 2)
    # ------------------------------------------------------------------
    # sale_order_id = fields.Many2one('sale.order', string='Pedido de Venta',
    #     readonly=True, copy=False)
    # partner_id = fields.Many2one('res.partner', string='Cliente', ...)
    # user_id = fields.Many2one('res.users', string='Vendedor', ...)

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    @api.depends("line_ids.line_total")
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped("line_total"))

    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = seq.next_by_code(
                    "matriz.almonte.pda.sale.import"
                ) or _("Nuevo")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------

    @api.model
    def create_from_payload(self, payload_dict, token_rec):
        """Crea o detecta duplicado a partir de un payload ya validado.

        :param payload_dict: dict Python con el payload parseado.
        :param token_rec: recordset ``matriz.almonte.api.token``.
        :returns: tuple (import_record, is_duplicate)
        """
        payload_json = json.dumps(payload_dict, sort_keys=True, ensure_ascii=False)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        # Soporte tanto para el campo 'uuid' del JSON real como 'external_reference' genérico
        external_ref = (
            payload_dict.get("uuid")
            or payload_dict.get("external_reference")
            or ""
        )

        # -- Detección de duplicados ----------------------------------------
        # Estrategia: misma external_reference + mismo token.
        # Refuerzo adicional: mismo payload_hash (detecta reenvíos exactos
        # aunque cambien de token).
        existing = self.sudo().search(
            [
                ("external_reference", "=", external_ref),
                ("token_id", "=", token_rec.id),
                ("state", "not in", ["error"]),
            ],
            limit=1,
        )
        if existing:
            _logger.info(
                "PDA Import: duplicado detectado external_ref=%s token=%s existing_id=%d",
                external_ref,
                token_rec.name,
                existing.id,
            )
            return existing, True

        # Comprobación adicional por hash (reenvío desde otro token)
        if payload_hash:
            hash_dup = self.sudo().search(
                [
                    ("payload_hash", "=", payload_hash),
                    ("state", "not in", ["error"]),
                ],
                limit=1,
            )
            if hash_dup:
                _logger.info(
                    "PDA Import: duplicado por hash external_ref=%s hash=%s",
                    external_ref,
                    payload_hash[:12] + "...",
                )
                return hash_dup, True

        # -- Parseo de fecha de operación ------------------------------------
        # Formato de la PDA: "DD/MM/YYYY HH:MM:SS"  (p.ej. "15/02/2024 10:11:15")
        # Fallback a ISO si viene en otro formato.
        operation_dt = False
        raw_dt = payload_dict.get("fecha_hora") or payload_dict.get("operation_datetime")
        if raw_dt:
            for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    from datetime import datetime as _dt
                    operation_dt = _dt.strptime(str(raw_dt).strip(), fmt)
                    break
                except ValueError:
                    continue

        # -- Construir log de procesamiento ----------------------------------
        log_lines = [
            f"[RECEIVED] uuid={external_ref}",
            f"[RECEIVED] usuario={payload_dict.get('usuario', '')}",
            f"[RECEIVED] token={token_rec.name}",
            f"[RECEIVED] lineas={len(payload_dict.get('lineas', []))}",
            f"[RECEIVED] fpago={payload_dict.get('fpago', '')}",
            f"[RECEIVED] descuento={payload_dict.get('descuento', 0)}",
        ]

        # -- Crear cabecera --------------------------------------------------
        import_vals = {
            "external_reference": external_ref,
            "pda_id": int(payload_dict.get("id", 0) or 0),
            "payload_hash": payload_hash,
            "payload_raw": json.dumps(payload_dict, indent=2, ensure_ascii=False),
            "token_id": token_rec.id,
            "device_code": payload_dict.get("device_code", ""),
            "operation_datetime": operation_dt,
            "salesperson_code": str(payload_dict.get("usuario", "")).strip(),
            "customer_reference": payload_dict.get("customer_reference", ""),
            "payment_method": str(payload_dict.get("fpago", "")).strip(),
            "global_discount": float(payload_dict.get("descuento", 0) or 0),
            "print_ticket": bool(payload_dict.get("imprimir", False)),
            "currency": payload_dict.get("currency", "EUR"),
            "notes": payload_dict.get("notes", ""),
            "state": "received",
            "processing_log": "\n".join(log_lines),
        }

        import_rec = self.sudo().create(import_vals)

        # -- Crear líneas ---------------------------------------------------
        # Soporte para el campo "lineas" del JSON real de la PDA.
        line_model = self.env["matriz.almonte.pda.sale.import.line"]
        raw_lines = payload_dict.get("lineas") or payload_dict.get("lines") or []
        for line in raw_lines:
            line_model.sudo().create(
                {
                    "import_id": import_rec.id,
                    "sequence": int(line.get("numero", 0) or 0),
                    "product_code": str(line.get("id_articulo", "") or line.get("product_code", "")).strip(),
                    "description": line.get("description", ""),
                    "qty": float(line.get("unidades", line.get("qty", 0)) or 0),
                    "unit_price": float(line.get("precio", line.get("unit_price", 0)) or 0),
                    "discount": float(line.get("discount", 0) or 0),
                    "line_uuid": str(line.get("uuid", "") or ""),
                    "line_total": float(line.get("line_total", 0) or 0),
                }
            )

        # Calcular y persistir el total sumando los totales de línea
        total = sum(
            float(line.get("unidades", line.get("qty", 0)) or 0)
            * float(line.get("precio", line.get("unit_price", 0)) or 0)
            * (1.0 - float(line.get("discount", 0) or 0) / 100.0)
            for line in raw_lines
        )
        import_rec.sudo().write({"total_amount": total})

        _logger.info(
            "PDA Import: creado import_id=%d uuid=%s lineas=%d total=%.2f",
            import_rec.id,
            external_ref,
            len(raw_lines),
            total,
        )
        return import_rec, False

    def action_retry_processing(self):
        """Botón backend: reintenta el procesamiento de registros en error."""
        for rec in self.filtered(lambda r: r.state == "error"):
            rec.write(
                {
                    "state": "received",
                    "error_message": False,
                    "processing_log": (rec.processing_log or "")
                    + "\n[RETRY] Reintento manual solicitado.",
                }
            )
        return True

    def action_view_lines(self):
        """Smart button para ver las líneas de esta importación."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Líneas - %(name)s", name=self.name),
            "res_model": "matriz.almonte.pda.sale.import.line",
            "view_mode": "list,form",
            "domain": [("import_id", "=", self.id)],
            "context": {"default_import_id": self.id},
        }
