/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, onWillUnmount } from "@odoo/owl";
import { PosOrderListController } from "@pos_conventional_core/js/pos_order_list_controller";

/**
 * Refresca automáticamente la lista de "Pedidos" del TPV en el backend
 * cuando llega un pedido nuevo desde la PDA, sin que haga falta recargar
 * la página a mano.
 *
 * El backend (``pda_pos_order_controller.py``) publica una notificación
 * "PDA_NEW_ORDER" en el bus de CADA punto de venta (mismo canal
 * ``pos_config.access_token`` que ya usa la impresión automática QZ Tray).
 * Aquí nos suscribimos a la de todos los puntos de venta -igual que hace
 * ``pda_auto_print_service``- y, al recibir cualquiera, recargamos el
 * modelo de la lista: si el pedido no es del punto de venta que se está
 * viendo, el filtro de la propia vista simplemente no lo mostrará.
 */
patch(PosOrderListController.prototype, {
    setup() {
        super.setup();
        this.busService = useService("bus_service");
        this.orm = useService("orm");
        // Referencia estable del callback: subscribe()/unsubscribe() del
        // bus_service lo usan como clave, así que hace falta guardarlo tal
        // cual para poder darlo de baja al desmontar (una arrow function
        // nueva en cada llamada no se podría desuscribir).
        this._pdaMounted = true;
        this._pdaReloading = false;
        this._pdaReloadPending = false;
        this._pdaNewOrderCallback = () => this._pdaReloadList();
        this._pdaNewOrderNotificationTypes = [];

        onWillStart(async () => {
            await this._subscribeToPdaNewOrders();
        });

        onWillUnmount(() => {
            // A partir de aquí ninguna recarga pendiente debe tocar la lista:
            // si llega un aviso mientras se cierra la pantalla, el modelo ya
            // está destruido y recargarlo lanzaba "Component is destroyed".
            this._pdaMounted = false;
            // No se llama a busService.deleteChannel(): el mismo canal
            // (pos_config.access_token) lo sigue necesitando el servicio de
            // impresión automática (pda_auto_print_service), que vive todo
            // el tiempo que dure la sesión del backend. Solo se retira la
            // suscripción propia de esta vista.
            for (const notificationType of this._pdaNewOrderNotificationTypes) {
                this.busService.unsubscribe(notificationType, this._pdaNewOrderCallback);
            }
        });
    },

    /**
     * Recarga la lista sin dejar que un fallo suba como error no capturado.
     *
     * - Si la vista ya se cerró, no hace nada.
     * - Si ya hay una recarga en curso (varias ventas seguidas), no lanza
     *   otra en paralelo: encadena una sola más al terminar.
     * - Si la vista se destruye mientras recarga, se ignora el error.
     */
    async _pdaReloadList() {
        if (!this._pdaMounted) {
            return;
        }
        if (this._pdaReloading) {
            this._pdaReloadPending = true;
            return;
        }
        this._pdaReloading = true;
        try {
            await this.model.load();
        } catch (error) {
            if (this._pdaMounted) {
                console.warn("[PDA][NewOrderRefresh] No se pudo recargar la lista", error);
            }
        } finally {
            this._pdaReloading = false;
            if (this._pdaReloadPending && this._pdaMounted) {
                this._pdaReloadPending = false;
                this._pdaReloadList();
            }
        }
    },

    async _subscribeToPdaNewOrders() {
        let configs = [];
        try {
            configs = await this.orm.searchRead("pos.config", [], ["access_token"]);
        } catch (error) {
            console.error(
                "[PDA][NewOrderRefresh] No se pudieron leer los puntos de venta",
                error
            );
            return;
        }

        for (const config of configs) {
            if (!config.access_token) {
                continue;
            }
            this.busService.addChannel(config.access_token);
            const notificationType = `${config.access_token}-PDA_NEW_ORDER`;
            this._pdaNewOrderNotificationTypes.push(notificationType);
            this.busService.subscribe(notificationType, this._pdaNewOrderCallback);
        }
    },
});
