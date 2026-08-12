/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Servicio de impresión automática para pedidos importados desde la PDA.
 * ------------------------------------------------------------------------
 * Cuando un pedido llega a Odoo desde la PDA (endpoint
 * ``/api/matriz_almonte/pda/pos/order``) y ya está completamente integrado
 * (albarán + factura simplificada generados), el backend
 * (``matriz_almonte_pda_sale_import``) necesita imprimir físicamente el
 * ticket en la impresora térmica de la tienda, EXACTAMENTE igual que
 * ocurre al pulsar el botón manual "Factura simplificada 80mm" del
 * formulario del pedido.
 *
 * En lugar de reimplementar la lógica de impresión (QZ Tray, IPP/CUPS,
 * etc. — depende de qué módulo de impresión directa esté instalado:
 * ``base_report_to_printer_qztray``, ``pos_printing_qztray`` u otro), este
 * servicio simplemente REPRODUCE el click manual:
 *
 *   1. El backend publica en el bus una notificación con el ``order_id``
 *      (canal ``pos_config.access_token``, el mismo que usa
 *      internamente ``point_of_sale`` vía ``pos.bus.mixin``).
 *   2. Este servicio (cargado en el backend general, ``web.assets_backend``
 *      — el MISMO contexto donde vive el botón "Factura simplificada
 *      80mm" y donde el módulo de impresión directa está activo) recibe
 *      la notificación.
 *   3. Llama por RPC a ``action_print_factura_simplificada`` (la MISMA
 *      acción que el botón) y ejecuta el resultado con
 *      ``action.doAction()`` — el MISMO mecanismo que usa Odoo al pulsar
 *      cualquier botón que devuelve una acción de informe. Si hay una
 *      impresora configurada (QZ Tray o la que sea) para ese informe, se
 *      imprime directamente, igual que en el click manual.
 *
 * IMPORTANTE: requiere que haya una sesión de Odoo (backend, no
 * necesariamente el POS táctil) abierta en un navegador del PC de la
 * tienda. Cada intento se confirma (ACK) al servidor llamando a
 * ``/api/matriz_almonte/pda/pos/print_ack``, visible en el campo "Estado
 * impresión PDA" del pedido.
 */
export const pdaAutoPrintService = {
    dependencies: ["orm", "bus_service", "action"],

    start(env, { orm, bus_service, action }) {
        this._subscribeToAllPosConfigs(orm, bus_service, action);
        return {};
    },

    async _subscribeToAllPosConfigs(orm, bus_service, action) {
        let configs = [];
        try {
            configs = await orm.searchRead(
                "pos.config",
                [],
                ["access_token", "name"]
            );
        } catch (error) {
            console.error(
                "[PDA][AutoPrint] No se pudieron leer los pos.config para " +
                    "suscribirse a la impresión automática:",
                error
            );
            return;
        }

        for (const config of configs) {
            if (!config.access_token) {
                continue;
            }
            bus_service.addChannel(config.access_token);
            bus_service.subscribe(
                `${config.access_token}-PDA_PRINT_TICKET`,
                (payload) => this._onPdaPrintTicket(payload, orm, action)
            );
        }

        // eslint-disable-next-line no-console
        console.info(
            "%c[PDA][AutoPrint] Servicio de impresión automática REGISTRADO " +
                `para ${configs.length} punto(s) de venta.`,
            "color: #28ffeb; font-weight: bold;"
        );
    },

    async _onPdaPrintTicket(payload, orm, action) {
        if (!payload || !payload.order_id) {
            return;
        }
        const orderLabel = payload.order_name || payload.order_id;
        console.info(
            `[PDA][AutoPrint] Notificación recibida: imprimir pedido ${orderLabel}.`
        );

        try {
            const reportAction = await orm.call(
                "pos.order",
                "action_print_factura_simplificada",
                [[payload.order_id]]
            );
            if (!reportAction) {
                await this._ackPrint(
                    orm,
                    payload.order_id,
                    false,
                    "action_print_factura_simplificada no devolvió ninguna acción."
                );
                return;
            }
            await action.doAction(reportAction);
            console.info(
                `[PDA][AutoPrint] Acción de impresión ejecutada para el ` +
                    `pedido ${orderLabel}.`
            );
            await this._ackPrint(orm, payload.order_id, true, "");
        } catch (error) {
            const msg = error?.message?.message || error?.message || String(error);
            console.error(
                `[PDA][AutoPrint] Error imprimiendo el pedido ${orderLabel}:`,
                error
            );
            await this._ackPrint(orm, payload.order_id, false, msg);
        }
    },

    async _ackPrint(orm, orderId, success, message) {
        try {
            await orm.call("pos.order", "pda_print_ack_rpc", [
                [orderId],
                success,
                message ? String(message).slice(0, 250) : "",
                "backend_doAction",
            ]);
        } catch (ackError) {
            console.error("[PDA][AutoPrint] No se pudo enviar el ACK:", ackError);
        }
    },
};

registry.category("services").add("pda_auto_print_service", pdaAutoPrintService);




