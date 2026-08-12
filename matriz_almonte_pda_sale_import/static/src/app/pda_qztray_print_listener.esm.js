/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

/**
 * Listener de impresión automática para pedidos importados desde la PDA.
 * ------------------------------------------------------------------------
 * Cuando un pedido llega a Odoo desde la PDA (endpoint
 * ``/api/matriz_almonte/pda/pos/order``) y ya está completamente integrado
 * (albarán + factura simplificada generados), el backend
 * (``matriz_almonte_pda_sale_import``) necesita imprimir físicamente el
 * ticket en la impresora térmica de la tienda.
 *
 * Cuando esa impresora está conectada por USB (sin IP de red propia) y el
 * mecanismo de impresión real es QZ Tray (exactamente igual que el botón
 * manual "Factura simplificada 80mm" del formulario del pedido), el
 * servidor NO puede conectar directamente con QZ Tray: ese servicio
 * escucha en localhost del PC de la tienda, no es alcanzable desde el
 * servidor Odoo.
 *
 * La solución: el backend publica los bytes ESC/POS ya generados (a
 * partir del PDF real de la factura simplificada) en el mismo canal en
 * tiempo real que usa internamente el POS
 * (``pos_config.access_token``, vía ``pos.bus.mixin``), con el evento
 * ``PDA_PRINT_TICKET``. Este listener, activo mientras el POS de la
 * tienda esté abierto en un navegador (donde QZ Tray sí está disponible),
 * recibe esa notificación y llama a ``QZConnection.print()`` — el MISMO
 * mecanismo que usa el módulo ``pos_printing_qztray`` para los tickets de
 * venta normales — para imprimir el ticket sin intervención manual.
 *
 * El import de QZConnection es DINÁMICO a propósito: si el módulo
 * ``pos_printing_qztray`` no está instalado en un TPV concreto, este
 * listener simplemente registra un aviso en consola y no hace nada, sin
 * romper el resto del POS.
 */
patch(PosStore.prototype, {
    async setup(env, deps) {
        await super.setup(env, deps);
        this._setupPdaQzTrayPrintListener();
    },

    _setupPdaQzTrayPrintListener() {
        try {
            this.data.connectWebSocket(
                "PDA_PRINT_TICKET",
                this._onPdaPrintTicket.bind(this)
            );
            console.info(
                "[PDA][QZTray] Listener de impresión automática registrado " +
                    "(canal PDA_PRINT_TICKET)."
            );
        } catch (error) {
            console.error(
                "[PDA][QZTray] No se pudo registrar el listener de impresión " +
                    "automática de pedidos PDA:",
                error
            );
        }
    },

    async _onPdaPrintTicket(payload) {
        if (!payload || !payload.escpos_base64) {
            return;
        }

        const orderLabel = payload.order_name || payload.order_id || "?";
        console.info(
            `[PDA][QZTray] Ticket recibido para imprimir (pedido ${orderLabel}).`
        );

        let QZConnection;
        try {
            ({ QZConnection } = await import(
                "@pos_printing_qztray/app/printer/qz_tray_connection.esm"
            ));
        } catch (error) {
            console.error(
                "[PDA][QZTray] El módulo 'pos_printing_qztray' no está " +
                    "disponible en este POS; no se puede imprimir " +
                    `automáticamente el ticket del pedido ${orderLabel}.`,
                error
            );
            return;
        }

        try {
            await QZConnection.print(payload.printer_name || "QZTray", [
                {
                    type: "raw",
                    format: "base64",
                    data: payload.escpos_base64,
                },
            ]);
            console.info(
                `[PDA][QZTray] Ticket del pedido ${orderLabel} impreso ` +
                    "correctamente vía QZ Tray."
            );
        } catch (error) {
            console.error(
                `[PDA][QZTray] Error imprimiendo el ticket del pedido ` +
                    `${orderLabel} vía QZ Tray:`,
                error
            );
        } finally {
            try {
                await QZConnection.disconnect();
            } catch {
                /* Ignorar errores de desconexión */
            }
        }
    },
});

