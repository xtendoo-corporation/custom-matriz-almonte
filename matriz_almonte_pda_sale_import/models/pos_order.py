# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Extensión de ``pos.order`` para finalizar pedidos importados desde PDA.

Reproduce el cierre estándar de un pedido de Punto de Venta (albarán de entrega
y factura simplificada) para las operaciones que llegan por la API de la PDA,
que crean el pedido de forma programática sin pasar por ``_process_saved_order``.
"""

import logging

from odoo import _, fields, models
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
