/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

const SEP = "*".repeat(80);
function log(title, ...details) {
    console.info(SEP);
    console.info(`[PDA][QZTray][POS] ${title}`);
    details.forEach((detail) => console.info(detail));
    console.info(SEP);
}

patch(PosStore.prototype, {
    async setup(...args) {
        await super.setup(...args);
        this._pdaPrintOrdersProcessed = new Set();
        this.data.connectWebSocket(
            "PDA_PRINT_TICKET",
            this._onPdaQzTrayTicket.bind(this)
        );
        log("LISTENER NATIVO POS REGISTRADO", `POS: ${this.config?.name || "?"}`);
        this._loadPendingPdaPrintJobs();
    },

    async _loadPendingPdaPrintJobs() {
        try {
            const jobs = await this.data.orm.call(
                "pos.order",
                "pda_get_pending_print_jobs",
                [this.config.id]
            );
            log("TRABAJOS PENDIENTES RECUPERADOS", `Cantidad: ${jobs.length}`, jobs);
            for (const job of jobs) {
                await this._onPdaQzTrayTicket(job);
            }
        } catch (error) {
            console.error(SEP);
            console.error("[PDA][QZTray][POS] ERROR RECUPERANDO PENDIENTES", error);
            console.error(SEP);
        }
    },

    async _onPdaQzTrayTicket(payload) {
        if (!payload?.order_id) {
            return;
        }
        if (this._pdaPrintOrdersProcessed.has(payload.order_id)) {
            log("TRABAJO DUPLICADO IGNORADO", `Pedido ID: ${payload.order_id}`);
            return;
        }
        this._pdaPrintOrdersProcessed.add(payload.order_id);
        const printAction = payload.print_action;
        log("NOTIFICACIÓN RECIBIDA", payload);
        if (!printAction || printAction.type !== "ir.actions.client") {
            const message = "La notificación no contiene una acción cliente QZ Tray.";
            console.error(SEP, message, payload, SEP);
            await this.data.orm.call("pos.order", "pda_print_ack_rpc", [
                [payload.order_id],
                false,
                message,
                "qztray_pos_client_action",
            ]);
            return;
        }
        try {
            log("EJECUTANDO ACCIÓN QZ TRAY", printAction);
            await this.action.doAction(printAction);
            log("ACCIÓN QZ TRAY FINALIZADA", printAction.tag);
            await this.data.orm.call("pos.order", "pda_print_ack_rpc", [
                [payload.order_id],
                true,
                `Acción cliente ejecutada desde POS: ${printAction.tag}`,
                "qztray_pos_client_action",
            ]);
        } catch (error) {
            console.error(SEP);
            console.error("[PDA][QZTray][POS] ERROR", error);
            console.error(SEP);
            const message = error?.message?.message || error?.message || String(error);
            try {
                await this.data.orm.call("pos.order", "pda_print_ack_rpc", [
                    [payload.order_id],
                    false,
                    String(message).slice(0, 250),
                    "qztray_pos_client_action",
                ]);
            } catch (ackError) {
                console.error("[PDA][QZTray][POS] ERROR ENVIANDO ACK", ackError);
            }
        }
    },
});

