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
        if self.account_move:
            return self.account_move

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
        self.with_context(generate_pdf=False)._generate_pos_order_invoice()
        _logger.info(
            "PDA Import: pedido %s facturado como factura simplificada %s",
            self.name,
            self.account_move.name,
        )
        return self.account_move
