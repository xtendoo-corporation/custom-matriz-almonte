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
 * IMPORTANTE (lección aprendida): ese botón NO usa el flujo estándar de
 * Odoo para acciones de informe (``doAction`` de un ``ir.actions.report``
 * normal). El módulo ``pos_conventional`` INTERCEPTA el click
 * (``pos_order_form_barcode_controller.js``) y en su lugar:
 *   1. Pide por RPC la URL HTML del informe (``get_factura_report_url``).
 *   2. Carga esa URL en un <iframe> oculto y llama a
 *      ``iframe.contentWindow.print()`` (``pos_print_iframe.js``, función
 *      registrada como ``registry.category("utils").get("pos_print_iframe")``).
 * Es ESE ``window.print()`` sobre el iframe el que, en el PC de la
 * tienda, acaba imprimiendo directamente (vía QZ Tray configurado como
 * impresora del sistema, o cualquier mecanismo de impresión silenciosa
 * que tengan configurado en el navegador/SO) — no una intercepción de
 * ``ir.actions.report`` a nivel de Odoo. Por eso disparar la acción con
 * ``doAction()`` NO reproducía el mismo comportamiento (como mucho abre o
 * descarga el PDF, pero no imprime directamente).
 *
 * Este servicio reproduce EXACTAMENTE esos mismos dos pasos, disparados
 * por una notificación del bus en lugar de por un click:
 *
 *   1. El backend publica en el bus una notificación con el ``order_id``
 *      (canal ``pos_config.access_token``, el mismo que usa
 *      internamente ``point_of_sale`` vía ``pos.bus.mixin``).
 *   2. Este servicio (cargado en el backend general, ``web.assets_backend``
 *      — el mismo contexto donde vive el botón manual) recibe la
 *      notificación.
 *   3. Pide la URL del informe y la imprime vía iframe oculto, igual que
 *      el botón. Si por lo que sea la función de ``pos_conventional`` no
 *      está disponible, cae a ``doAction()`` como plan B.
 *
 * IMPORTANTE: requiere que haya una sesión de Odoo (backend) abierta en
 * un navegador del PC de la tienda. Cada intento se confirma (ACK) al
 * servidor llamando a ``pda_print_ack_rpc``, visible en el campo "Estado
 * impresión PDA" del pedido.
 */

/**
 * Reimplementación local (fallback) de ``openUrlInHiddenPrintIframe`` de
 * ``pos_conventional/static/src/js/pos_print_iframe.js``, por si ese
 * módulo no registrara la utilidad (nunca debería pasar si
 * ``pos_conventional`` está instalado, pero así este servicio no depende
 * en tiempo de carga de ningún otro módulo).
 */
function fallbackPrintIframe(url) {
    return new Promise((resolve, reject) => {
        try {
            const iframe = document.createElement("iframe");
            iframe.style.position = "fixed";
            iframe.style.right = "0";
            iframe.style.bottom = "0";
            iframe.style.width = "1px";
            iframe.style.height = "1px";
            iframe.style.border = "0";
            iframe.style.opacity = "0";
            iframe.style.pointerEvents = "none";
            iframe.src = url;
            iframe.onload = () => {
                setTimeout(() => {
                    try {
                        iframe.contentWindow.focus();
                        iframe.contentWindow.print();
                        setTimeout(() => {
                            try {
                                iframe.remove();
                            } catch {
                                /* noop */
                            }
                            resolve(true);
                        }, 500);
                    } catch (e) {
                        try {
                            iframe.remove();
                        } catch {
                            /* noop */
                        }
                        reject(e);
                    }
                }, 50);
            };
            iframe.onerror = (err) => {
                try {
                    iframe.remove();
                } catch {
                    /* noop */
                }
                reject(err);
            };
            document.body.appendChild(iframe);
        } catch (e) {
            reject(e);
        }
    });
}

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

        // ====== MÉTODO 1: EXACTAMENTE igual que el botón manual ======
        // get_factura_report_url() + iframe oculto + window.print().
        try {
            const url = await orm.call("pos.order", "get_factura_report_url", [
                [payload.order_id],
            ]);
            if (url) {
                const absoluteUrl = new URL(url, window.location.origin).toString();
                const printIframeFn =
                    registry.category("utils").get("pos_print_iframe", null) ||
                    fallbackPrintIframe;

                await printIframeFn(`${absoluteUrl}?download=false`);
                console.info(
                    `[PDA][AutoPrint] Ticket del pedido ${orderLabel} enviado a ` +
                        "imprimir vía iframe (igual que el botón manual)."
                );
                await this._ackPrint(orm, payload.order_id, true, "", "iframe_print");
                return;
            }
            console.warn(
                `[PDA][AutoPrint] get_factura_report_url no devolvió URL para ` +
                    `el pedido ${orderLabel}; probando plan B (doAction).`
            );
        } catch (error) {
            console.error(
                `[PDA][AutoPrint] Error imprimiendo (iframe) el pedido ` +
                    `${orderLabel}, probando plan B (doAction):`,
                error
            );
        }

        // ====== MÉTODO 2 (plan B): doAction() sobre la acción de informe.
        // Puede no disparar impresión silenciosa (solo abrir/descargar el
        // PDF), pero al menos deja el documento accesible al usuario. ===
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
                    "Ni el iframe ni action_print_factura_simplificada " +
                        "devolvieron un resultado utilizable.",
                    "doaction_fallback"
                );
                return;
            }
            await action.doAction(reportAction);
            console.info(
                `[PDA][AutoPrint] (Plan B) Acción de impresión ejecutada para ` +
                    `el pedido ${orderLabel}.`
            );
            await this._ackPrint(
                orm,
                payload.order_id,
                true,
                "Impreso vía doAction (plan B, no vía iframe).",
                "doaction_fallback"
            );
        } catch (error) {
            const msg = error?.message?.message || error?.message || String(error);
            console.error(
                `[PDA][AutoPrint] Error imprimiendo (plan B) el pedido ` +
                    `${orderLabel}:`,
                error
            );
            await this._ackPrint(orm, payload.order_id, false, msg, "doaction_fallback");
        }
    },

    async _ackPrint(orm, orderId, success, message, stage) {
        try {
            await orm.call("pos.order", "pda_print_ack_rpc", [
                [orderId],
                success,
                message ? String(message).slice(0, 250) : "",
                stage || "print",
            ]);
        } catch (ackError) {
            console.error("[PDA][AutoPrint] No se pudo enviar el ACK:", ackError);
        }
    },
};

registry.category("services").add("pda_auto_print_service", pdaAutoPrintService);



