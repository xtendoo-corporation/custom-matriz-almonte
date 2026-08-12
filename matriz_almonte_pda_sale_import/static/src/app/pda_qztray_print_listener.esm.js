/** @odoo-module **/

import { registry } from "@web/core/registry";

const DIAGNOSTIC_SEPARATOR = "*".repeat(80);

function diagnosticLog(level, title, details = []) {
    const logger = console[level] || console.log;
    logger.call(console, DIAGNOSTIC_SEPARATOR);
    logger.call(console, `[PDA][AutoPrint] ${title}`);
    for (const detail of details) {
        logger.call(console, detail);
    }
    logger.call(console, DIAGNOSTIC_SEPARATOR);
}

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
 * En producción el módulo ``pos_conventional_qztray`` hace que el método
 * del botón devuelva una acción cliente específica:
 * ``pos_conventional_print_receipt_qztray_window``. Esa acción es quien
 * prepara el recibo original y lo envía a QZ Tray, por lo que debe
 * ejecutarse con ``action.doAction()`` sin transformarla en informe ni
 * iframe. Algunas instalaciones sin esa extensión devuelven en cambio un
 * ``ir.actions.report``; para ellas se conserva el flujo histórico de
 * URL HTML + iframe oculto + ``window.print()``.
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
 *   3. Invoca ``action_print_factura_simplificada`` y ejecuta exactamente
 *      la acción devuelta: acción cliente QZ Tray en producción, o iframe
 *      para acciones de informe en instalaciones compatibles.
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
            diagnosticLog("info", "CREANDO IFRAME OCULTO DE IMPRESIÓN", [
                `URL: ${url}`,
            ]);
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
                diagnosticLog("info", "IFRAME CARGADO; PREPARANDO window.print()", [
                    `URL cargada: ${url}`,
                ]);
                setTimeout(() => {
                    try {
                        iframe.contentWindow.focus();
                        iframe.contentWindow.print();
                        diagnosticLog("info", "window.print() EJECUTADO", [
                            "La orden de impresión se ha enviado desde el iframe.",
                        ]);
                        setTimeout(() => {
                            try {
                                iframe.remove();
                            } catch {
                                /* noop */
                            }
                            resolve(true);
                        }, 500);
                    } catch (e) {
                        diagnosticLog("error", "ERROR EJECUTANDO window.print()", [e]);
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
                diagnosticLog("error", "ERROR CARGANDO EL IFRAME", [err]);
                try {
                    iframe.remove();
                } catch {
                    /* noop */
                }
                reject(err);
            };
            document.body.appendChild(iframe);
        } catch (e) {
            diagnosticLog("error", "ERROR CREANDO EL IFRAME", [e]);
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
            diagnosticLog(
                "error",
                "ERROR LEYENDO LOS PUNTOS DE VENTA PARA SUSCRIBIR EL LISTENER",
                [error]
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

        diagnosticLog("info", "SERVICIO DE IMPRESIÓN AUTOMÁTICA REGISTRADO", [
            `Puntos de venta suscritos: ${configs.length}`,
            ...configs.map(
                (config) =>
                    `POS: ${config.name} | canal: ${config.access_token || "SIN TOKEN"}`
            ),
        ]);
    },

    async _onPdaPrintTicket(payload, orm, action) {
        if (!payload || !payload.order_id) {
            return;
        }
        const orderLabel = payload.order_name || payload.order_id;
        diagnosticLog("info", "NOTIFICACIÓN DE IMPRESIÓN RECIBIDA", [
            `Pedido: ${orderLabel}`,
            `ID: ${payload.order_id}`,
            payload,
        ]);

        // ====== PASO 1: invocar EXACTAMENTE el método del botón manual. ====
        // En producción ``pos_conventional_qztray`` devuelve una acción
        // cliente con tag ``pos_conventional_print_receipt_qztray_window``.
        // Esa acción debe ejecutarse directamente: es quien conecta con QZ
        // Tray y envía el ticket original a la impresora.
        let printAction;
        try {
            diagnosticLog("info", "EJECUTANDO EL MÉTODO DEL BOTÓN MANUAL", [
                `Pedido: ${orderLabel}`,
                "Método: pos.order.action_print_factura_simplificada",
            ]);
            printAction = await orm.call(
                "pos.order",
                "action_print_factura_simplificada",
                [[payload.order_id]]
            );
            diagnosticLog("info", "ACCIÓN DE IMPRESIÓN RECIBIDA", [
                `Tipo: ${printAction?.type || "SIN TIPO"}`,
                `Tag: ${printAction?.tag || "SIN TAG"}`,
                printAction,
            ]);
        } catch (error) {
            const msg = error?.message?.message || error?.message || String(error);
            diagnosticLog("error", "ERROR EJECUTANDO EL MÉTODO DEL BOTÓN", [
                `Pedido: ${orderLabel}`,
                error,
            ]);
            await this._ackPrint(orm, payload.order_id, false, msg, "button_method");
            return;
        }

        if (!printAction) {
            await this._ackPrint(
                orm,
                payload.order_id,
                false,
                "action_print_factura_simplificada no devolvió ninguna acción.",
                "button_method"
            );
            return;
        }

        if (printAction.type === "ir.actions.client") {
            try {
                diagnosticLog("info", "EJECUTANDO ACCIÓN CLIENTE QZ TRAY", [
                    `Pedido: ${orderLabel}`,
                    `Tag: ${printAction.tag}`,
                    printAction.params || {},
                ]);
                await action.doAction(printAction);
                diagnosticLog("info", "ACCIÓN CLIENTE QZ TRAY FINALIZADA", [
                    `Pedido: ${orderLabel}`,
                    `Tag: ${printAction.tag}`,
                    "Se enviará ACK de éxito al servidor.",
                ]);
                await this._ackPrint(
                    orm,
                    payload.order_id,
                    true,
                    `Acción cliente ejecutada: ${printAction.tag}`,
                    "qztray_client_action"
                );
            } catch (error) {
                const msg = error?.message?.message || error?.message || String(error);
                diagnosticLog("error", "ERROR EJECUTANDO LA ACCIÓN CLIENTE QZ TRAY", [
                    `Pedido: ${orderLabel}`,
                    `Tag: ${printAction.tag}`,
                    error,
                ]);
                await this._ackPrint(
                    orm,
                    payload.order_id,
                    false,
                    msg,
                    "qztray_client_action"
                );
            }
            return;
        }

        if (printAction.type !== "ir.actions.report") {
            const msg = `Tipo de acción no imprimible: ${printAction.type || "vacío"}`;
            diagnosticLog("error", "TIPO DE ACCIÓN NO SOPORTADO", [msg, printAction]);
            await this._ackPrint(
                orm,
                payload.order_id,
                false,
                msg,
                "unsupported_action"
            );
            return;
        }

        // ====== COMPATIBILIDAD: algunas instalaciones devuelven un informe.
        // En ese caso reproducimos el mecanismo histórico de pos_conventional:
        // get_factura_report_url() + iframe oculto + window.print(). ======
        try {
            diagnosticLog("info", "ACCIÓN DE INFORME: SOLICITANDO URL HTML", [
                `Pedido: ${orderLabel}`,
                "Método: pos.order.get_factura_report_url",
            ]);
            const url = await orm.call("pos.order", "get_factura_report_url", [
                [payload.order_id],
            ]);
            if (url) {
                const absoluteUrl = new URL(url, window.location.origin).toString();
                const printIframeFn =
                    registry.category("utils").get("pos_print_iframe", null) ||
                    fallbackPrintIframe;

                diagnosticLog("info", "URL DEL INFORME OBTENIDA", [
                    `URL relativa: ${url}`,
                    `URL absoluta: ${absoluteUrl}`,
                    `Función de impresión: ${
                        printIframeFn === fallbackPrintIframe
                            ? "fallbackPrintIframe local"
                            : "pos_print_iframe de pos_conventional"
                    }`,
                ]);

                await printIframeFn(`${absoluteUrl}?download=false`);
                diagnosticLog("info", "IMPRESIÓN VÍA IFRAME FINALIZADA", [
                    `Pedido: ${orderLabel}`,
                    "Se enviará ACK de éxito al servidor.",
                ]);
                await this._ackPrint(orm, payload.order_id, true, "", "iframe_print");
                return;
            }
            diagnosticLog("warn", "EL MÉTODO NO DEVOLVIÓ URL; EJECUTANDO INFORME", [
                `Pedido: ${orderLabel}`,
            ]);
        } catch (error) {
            diagnosticLog("error", "ERROR EN IMPRESIÓN VÍA IFRAME; EJECUTANDO INFORME", [
                `Pedido: ${orderLabel}`,
                error,
            ]);
        }

        // ====== Plan B solo para acciones de informe: doAction().
        // Puede no disparar impresión silenciosa (solo abrir/descargar el
        // PDF), pero al menos deja el documento accesible al usuario. ===
        try {
            diagnosticLog("warn", "EJECUTANDO ACCIÓN DE INFORME COMO PLAN B", [
                `Pedido: ${orderLabel}`,
            ]);
            await action.doAction(printAction);
            diagnosticLog("warn", "PLAN B EJECUTADO", [
                `Pedido: ${orderLabel}`,
                printAction,
            ]);
            await this._ackPrint(
                orm,
                payload.order_id,
                true,
                "Impreso vía doAction (plan B, no vía iframe).",
                "doaction_fallback"
            );
        } catch (error) {
            const msg = error?.message?.message || error?.message || String(error);
            diagnosticLog("error", "ERROR TAMBIÉN EN EL PLAN B", [
                `Pedido: ${orderLabel}`,
                error,
            ]);
            await this._ackPrint(orm, payload.order_id, false, msg, "doaction_fallback");
        }
    },

    async _ackPrint(orm, orderId, success, message, stage) {
        try {
            diagnosticLog("info", "ENVIANDO ACK DE IMPRESIÓN AL SERVIDOR", [
                `Pedido ID: ${orderId}`,
                `Resultado: ${success ? "SUCCESS" : "ERROR"}`,
                `Etapa: ${stage || "print"}`,
                `Mensaje: ${message || "(vacío)"}`,
            ]);
            await orm.call("pos.order", "pda_print_ack_rpc", [
                [orderId],
                success,
                message ? String(message).slice(0, 250) : "",
                stage || "print",
            ]);
        } catch (ackError) {
            diagnosticLog("error", "ERROR ENVIANDO EL ACK AL SERVIDOR", [ackError]);
        }
    },
};

registry.category("services").add("pda_auto_print_service", pdaAutoPrintService);



