/* global qz */
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import {registry} from "@web/core/registry";

// base_report_to_printer_qztray crea la configuración de QZ sin opciones
// (``qz.configs.create(printer)``): QZ imprime entonces en el papel por
// defecto de la cola/driver y reescala el PDF, y la etiqueta sale pequeña y
// descentrada. Para los informes de etiquetas se fuerza aquí el papel y que
// no se escale, de modo que no haga falta configurar nada en cada equipo.
//
// El papel se declara 56 x 29 mm (ancho x alto): es la etiqueta horizontal
// de 50 mm más ~3 mm de margen de no impresión de la QL-700 en cada extremo
// de la cinta de 29 mm. Con 29 x 56 (vertical) la etiqueta sale girada.
// Los márgenes compensan la zona no imprimible de la QL-700 para que el
// contenido quede centrado.
const LABEL_REPORTS = [
    "matriz_almonte_product_label.product_label_report",
    "matriz_almonte_product_label.product_label_report_plata",
];
const LABEL_QZ_OPTIONS = {
    units: "mm",
    size: {width: 56, height: 29},
    scaleContent: false,
    margins: {top: 3, right: 0, bottom: 0, left: 2},
    colorType: "blackwhite",
    density: 300,
};

const backends = registry.category("report.print.backends");
const qzDispatcher = backends.get("qztray", null);

if (qzDispatcher) {
    backends.add(
        "qztray",
        async (action, env) => {
            if (!LABEL_REPORTS.includes(action.report_name)) {
                return qzDispatcher(action, env);
            }
            const createConfig = qz.configs.create;
            qz.configs.create = (printer, options) =>
                createConfig(printer, {...LABEL_QZ_OPTIONS, ...options});
            try {
                return await qzDispatcher(action, env);
            } finally {
                qz.configs.create = createConfig;
            }
        },
        {force: true}
    );
}
