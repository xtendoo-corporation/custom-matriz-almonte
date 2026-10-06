# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Extensión de ``pos.order`` para finalizar pedidos importados desde PDA.

Reproduce el cierre estándar de un pedido de Punto de Venta (albarán de entrega
y factura simplificada) para las operaciones que llegan por la API de la PDA,
que crean el pedido de forma programática sin pasar por ``_process_saved_order``.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    pda_external_reference = fields.Char(
        string="Referencia externa PDA",
        index=True,
        copy=False,
        help=(
            "Identificador del pedido tal y como llega desde la PDA "
            "(campo 'external_reference'). Permite localizar el pedido "
            "por su referencia de origen."
        ),
    )
    _pda_external_reference_unique = models.Constraint(
        "unique (pda_external_reference)",
        "Ya existe un pedido POS con esa referencia externa de la PDA.",
    )

    pda_invoice_state = fields.Selection(
        selection=[
            ("pending", "Pendiente"),
            ("done", "Facturado"),
            ("error", "Error (se reintentará)"),
        ],
        string="Estado factura diferida PDA",
        copy=False,
        readonly=True,
        index="btree_not_null",
        help="Solo se rellena cuando la facturación del pedido se difiere "
        "(ajuste 'Facturación diferida PDA'). Un cron reintenta los "
        "pedidos pendientes o con error.",
    )
    pda_invoice_attempts = fields.Integer(
        string="Intentos de facturación PDA", copy=False, readonly=True
    )
    pda_invoice_error = fields.Text(
        string="Último error de facturación PDA", copy=False, readonly=True
    )
    pda_amount_mismatch = fields.Boolean(
        string="Descuadre de importe PDA",
        copy=False,
        readonly=True,
        help="El total calculado por Odoo difiere del 'amount_total' que "
        "envió la PDA.",
    )
    pda_payload_amount_total = fields.Monetary(
        string="Total enviado por la PDA",
        copy=False,
        readonly=True,
        currency_field="currency_id",
    )
    pda_simplified_invoice_number = fields.Char(
        string="Nº factura simplificada (PDA)",
        index=True,
        copy=False,
        readonly=True,
        help=(
            "Número de la factura simplificada (account.move) generada al "
            "importar el pedido desde la PDA. Se guarda para poder "
            "identificarla/reimprimirla fácilmente."
        ),
    )
    pda_print_mode = fields.Char(
        string="Método de impresión PDA",
        copy=False,
        readonly=True,
        help="Método usado en el último intento de impresión automática "
        "del pedido (backend_action_bus, direct_tcp, bridge, etc.).",
    )
    pda_print_ack_state = fields.Selection(
        selection=[
            ("pending", "Pendiente de confirmación"),
            ("success", "Impreso correctamente"),
            ("error", "Error al imprimir"),
        ],
        string="Estado impresión PDA",
        copy=False,
        readonly=True,
        help="Confirmación real, enviada por el navegador con la sesión de "
        "Odoo abierta en la tienda, de si se pudo reproducir la impresión "
        "automática (llamando a la misma acción que el botón 'Factura "
        "simplificada 80mm'). 'Pendiente' significa que el servidor "
        "publicó la notificación en el bus pero ningún navegador ha "
        "confirmado (aún) haberla recibido/procesado: lo más probable es "
        "que no haya ninguna sesión de Odoo abierta en la tienda en ese "
        "momento, o que el bundle de assets no se haya actualizado tras "
        "el último despliegue.",
    )
    pda_print_ack_message = fields.Char(
        string="Detalle impresión PDA",
        copy=False,
        readonly=True,
    )
    pda_print_ack_date = fields.Datetime(
        string="Fecha confirmación impresión PDA",
        copy=False,
        readonly=True,
    )
    pda_print_action = fields.Json(
        string="Acción pendiente de impresión PDA",
        copy=False,
        readonly=True,
    )
    pda_payment_type = fields.Char(
        string="Tipo de pago PDA",
        copy=False,
        readonly=True,
        help=(
            "Tipo de pago tal y como lo declaró la PDA (campo "
            "'payment_type' del JSON): 'cash', 'card', 'combined', etc. "
            "Permite identificar fácilmente los pedidos pagados con más "
            "de un método (pago combinado efectivo + tarjeta)."
        ),
    )
    pda_amount_cash = fields.Monetary(
        string="Importe efectivo (PDA)",
        copy=False,
        readonly=True,
        currency_field="currency_id",
        help=(
            "Parte del importe cobrada en efectivo según la PDA (campo "
            "'amount_cash' del JSON), en un pago combinado."
        ),
    )
    pda_amount_card = fields.Monetary(
        string="Importe tarjeta (PDA)",
        copy=False,
        readonly=True,
        currency_field="currency_id",
        help=(
            "Parte del importe cobrada con tarjeta según la PDA (campo "
            "'amount_card' del JSON), en un pago combinado."
        ),
    )

    @api.model
    def pda_get_pending_print_jobs(self, config_id):
        """Devuelve trabajos QZ Tray pendientes del POS indicado.

        Es el respaldo persistente del bus: si la notificación en tiempo real
        se pierde, el frontend la recupera al arrancar o recargar el POS.
        """
        orders = self.sudo().search(
            [
                ("config_id", "=", int(config_id)),
                ("pda_print_ack_state", "=", "pending"),
            ],
            order="id asc",
            limit=50,
        )
        jobs = []
        for order in orders:
            action = order.pda_print_action
            if not action and hasattr(order, "action_print_factura_simplificada"):
                try:
                    action = order.with_context(
                        pda_raw_receipt=True
                    ).action_print_factura_simplificada()
                    if action:
                        order.sudo().pda_print_action = action
                except Exception:  # noqa: BLE001 - un trabajo no bloquea los demás
                    _logger.exception(
                        "[PDA ORDER] No se pudo regenerar la acción pendiente "
                        "del pedido %s.",
                        order.name,
                    )
            if not action:
                continue
            jobs.append({
                "order_id": order.id,
                "order_name": order.name,
                "print_action": action,
            })
        return jobs

    def pda_print_ack_rpc(self, success, message="", stage="print"):
        """Confirmación (ACK) llamada por RPC desde el servicio JS
        ``pda_auto_print_service`` tras intentar reproducir la impresión
        automática del pedido (ver
        ``static/src/app/pda_qztray_print_listener.esm.js``).

        Permite diagnosticar de forma fiable el punto exacto del fallo:
        si el campo se queda en ``pending`` es que ningún navegador con
        una sesión de Odoo abierta llegó a recibir la notificación del
        bus; si llega ``error``, sí la recibió pero la acción de
        impresión falló (revisar ``message``).
        """
        self.ensure_one()
        state = "success" if success else "error"
        self.sudo().write(
            {
                "pda_print_ack_state": state,
                "pda_print_ack_message": (message or "")[:250],
                "pda_print_ack_date": fields.Datetime.now(),
            }
        )
        _logger.info(
            "[PDA ORDER] ACK de impresión recibido para el pedido %s "
            "(stage=%s): %s%s",
            self.name,
            stage or "print",
            state,
            f" - {message}" if message else "",
        )
        return True

    # ------------------------------------------------------------------
    # Facturación diferida
    # ------------------------------------------------------------------
    _PDA_INVOICE_MAX_ATTEMPTS = 10
    _PDA_INVOICE_BATCH = 20

    def pda_run_deferred_invoice(self):
        """Genera albarán + factura de un pedido, aislando el fallo.

        Cada pedido se procesa en su propio savepoint: si falla, el pedido
        sigue pagado, queda en estado ``error`` con el motivo y se
        reintenta en la siguiente pasada del cron.

        :returns: ``True`` si el pedido quedó facturado.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.matriz_almonte_generate_picking_and_invoice()
        except Exception as exc:  # noqa: BLE001 - nunca perder la venta
            _logger.exception(
                "[PDA ORDER] Falló la facturación diferida del pedido %s.",
                self.name,
            )
            self.write(
                {
                    "pda_invoice_state": "error",
                    "pda_invoice_attempts": self.pda_invoice_attempts + 1,
                    "pda_invoice_error": str(exc)[:2000],
                }
            )
            return False
        self.write(
            {
                "pda_invoice_state": "done",
                "pda_invoice_attempts": self.pda_invoice_attempts + 1,
                "pda_invoice_error": False,
            }
        )
        return True

    @api.model
    def _cron_pda_process_deferred_invoices(self):
        """Cron: factura los pedidos PDA con facturación pendiente o fallida."""
        orders = self.sudo().search(
            [
                ("pda_invoice_state", "in", ("pending", "error")),
                ("pda_invoice_attempts", "<", self._PDA_INVOICE_MAX_ATTEMPTS),
                ("state", "in", ("paid", "done", "invoiced")),
            ],
            order="id asc",
            limit=self._PDA_INVOICE_BATCH,
        )
        for order in orders:
            order.pda_run_deferred_invoice()
            # Confirma pedido a pedido: un fallo posterior no revierte lo ya
            # facturado.
            self.env.cr.commit()
        # Solo se re-dispara de inmediato si quedan pedidos nunca
        # intentados; los que fallaron esperan a la siguiente pasada
        # periódica para no entrar en un bucle rápido.
        remaining = self.sudo().search_count([("pda_invoice_state", "=", "pending")])
        if remaining:
            cron = self.env.ref(
                "matriz_almonte_pda_sale_import.ir_cron_pda_deferred_invoices",
                raise_if_not_found=False,
            )
            if cron:
                cron._trigger()
        return True

    def action_print_factura_simplificada(self):
        """Imprime desde la PDA con el ticket RAW (ESC/POS), no con el PDF.

        ``pos_conventional_qztray`` devuelve ``raw_receipt: False`` para este
        botón, lo que manda el informe como PDF a la impresora. En una cola
        térmica ``raw`` eso sale como un ticket interminable. El flujo normal
        del TPV usa el ticket RAW; aquí se fuerza lo mismo, pero solo cuando
        la llamada viene de la PDA (contexto ``pda_raw_receipt``), para no
        cambiar el botón manual del formulario del pedido.
        """
        action = super().action_print_factura_simplificada()
        if (
            self.env.context.get("pda_raw_receipt")
            and isinstance(action, dict)
            and action.get("type") == "ir.actions.client"
            and (action.get("params") or {}).get("use_qztray")
        ):
            params = dict(action["params"])
            params["raw_receipt"] = True
            params["print_original_receipt"] = False
            action["params"] = params
        return action

    def matriz_almonte_ensure_account_move(self):
        """Devuelve y, si es necesario, recupera la factura del pedido.

        ``account_move`` puede haberse leído como vacío antes de llamar a
        ``_generate_pos_order_invoice``. La factura se crea enlazando
        ``account.move.pos_order_ids``, pero el valor vacío puede permanecer
        en la caché del recordset durante la misma petición HTTP. Eso hacía
        que ``action_print_factura_simplificada`` devolviera ``None`` justo
        después de integrar el pedido, aunque la factura sí existiera.
        """
        self.ensure_one()
        self.flush_recordset()
        self.invalidate_recordset(["account_move"])
        move = self.account_move
        if not move:
            move = self.env["account.move"].sudo().search(
                [("pos_order_ids", "in", self.id)],
                order="id desc",
                limit=1,
            )
            if move:
                self.sudo().write({"account_move": move.id})
                self.invalidate_recordset(["account_move"])
                move = self.account_move
        return move


    def _force_create_picking_real_time(self):
        """Fuerza la creación del albarán en el momento de la importación.

        En configuraciones que actualizan el stock al cierre de sesión el
        albarán no se crearía hasta cerrar la sesión.  Para las ventas
        importadas desde la PDA queremos el albarán de forma inmediata, por lo
        que activamos la creación en tiempo real cuando el contexto lo indica.
        El *guard* de ``_create_order_picking`` evita duplicados al cierre.
        """
        if self.env.context.get("matriz_almonte_force_picking"):
            return True
        return super()._force_create_picking_real_time()

    def matriz_almonte_generate_picking_and_invoice(self):
        """Genera el albarán de entrega y la factura simplificada del pedido.

        Reproduce el cierre estándar de un pedido POS ya pagado:

        * Crea el picking de entrega (albarán).
        * Emite la factura simplificada en ``account.move``.

        Es idempotente: no duplica el albarán ni la factura si ya existen.

        :returns: recordset ``account.move`` de la factura simplificada.
        """
        self.ensure_one()

        if self.state not in ("paid", "done", "invoiced"):
            raise UserError(
                _(
                    "El pedido %(name)s debe estar pagado para generar el "
                    "albarán y la factura simplificada.",
                    name=self.name,
                )
            )

        # Albarán de entrega (idempotente vía guard interno de picking_ids).
        self.with_context(
            matriz_almonte_force_picking=True
        )._create_order_picking()

        # Factura simplificada.
        move = self.matriz_almonte_ensure_account_move()
        if move:
            if not self.pda_simplified_invoice_number:
                self.pda_simplified_invoice_number = move.name
            return move

        if not self.config_id.invoice_journal_id:
            raise UserError(
                _(
                    "El punto de venta '%(pos)s' no tiene un diario de "
                    "facturación configurado; no se puede emitir la factura "
                    "simplificada.",
                    pos=self.config_id.name,
                )
            )

        if not self.partner_id:
            raise UserError(
                _(
                    "No se puede emitir la factura simplificada del pedido "
                    "%(name)s sin un cliente asignado.",
                    name=self.name,
                )
            )

        self.write({"to_invoice": True})
        move = self.with_context(generate_pdf=False)._generate_pos_order_invoice()
        # Aunque la creación de account.move con ``pos_order_ids`` debería
        # establecer el inverso ``account_move``, lo escribimos explícitamente
        # para que quede disponible en esta misma transacción y recordset.
        if move and self.account_move != move:
            self.sudo().write({"account_move": move.id})
        self.flush_recordset(["account_move"])
        self.invalidate_recordset(["account_move"])
        move = self.matriz_almonte_ensure_account_move()
        if not move:
            raise UserError(
                _(
                    "Se creó la factura del pedido %(name)s, pero no se pudo "
                    "enlazar con el pedido POS.",
                    name=self.name,
                )
            )
        # Guardamos el número de la factura simplificada en el pedido para
        # poder identificarla/reimprimirla sin depender de recomputar el
        # enlace ``account_move``.
        self.pda_simplified_invoice_number = move.name
        _logger.info(
            "PDA Import: pedido %s facturado como %s %s",
            self.name,
            "factura rectificativa"
            if move.move_type == "out_refund"
            else "factura simplificada",
            move.name,
        )
        return move
