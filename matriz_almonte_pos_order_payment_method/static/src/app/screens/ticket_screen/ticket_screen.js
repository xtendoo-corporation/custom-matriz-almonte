/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";

patch(TicketScreen.prototype, {
    /**
     * Devuelve la forma (o formas) de pago usada(s) en el pedido, como una
     * cadena de nombres únicos separados por comas. Se usa en la nueva
     * columna de la lista de pedidos del Punto de Venta.
     */
    getPaymentMethods(order) {
        const names = [];
        for (const payment of order.payment_ids || []) {
            const name = payment.payment_method_id?.name;
            if (name && !names.includes(name)) {
                names.push(name);
            }
        }
        return names.join(", ");
    },
});

