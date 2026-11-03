/* global qz */
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
import {registry} from "@web/core/registry";

// base_report_to_printer_qztray crea la configuración de QZ sin opciones
// (``qz.configs.create(printer)``): QZ imprime entonces en el papel por
// defecto de la cola/driver y reescala el PDF, y la etiqueta sale pequeña y
// descentrada. Para los informes de etiquetas se fuerza aquí el papel y que
// no se escale, de modo que no haga falta configurar nada en cada equipo.
//
// El papel se declara <ancho etiqueta + 6> x 29 mm (ancho x alto): es la
// etiqueta horizontal más ~3 mm de margen de no impresión de la QL-700 en
// cada extremo de la cinta de 29 mm. Con el papel en vertical la etiqueta
// sale girada. Los márgenes compensan la zona no imprimible de la QL-700
// para que el contenido quede centrado.
const LABEL_QZ_BASE_OPTIONS = {
    units: "mm",
    scaleContent: false,
    margins: {top: 3, right: 0, bottom: 0, left: 2},
    colorType: "blackwhite",
    density: 300,
};
const PAPER_50X25 = {width: 56, height: 29};
const PAPER_40X25 = {width: 46, height: 29};
const LABEL_REPORT_PAPERS = {
    "matriz_almonte_product_label.product_label_report": PAPER_50X25,
    "matriz_almonte_product_label.product_label_report_plata": PAPER_50X25,
    "matriz_almonte_product_label.product_label_report_40x25": PAPER_40X25,
};

const backends = registry.category("report.print.backends");
const qzDispatcher = backends.get("qztray", null);

if (qzDispatcher) {
    backends.add(
        "qztray",
        async (action, env) => {
            const paper = LABEL_REPORT_PAPERS[action.report_name];
            if (!paper) {
                return qzDispatcher(action, env);
            }
            const labelOptions = {...LABEL_QZ_BASE_OPTIONS, size: paper};
            const createConfig = qz.configs.create;
            qz.configs.create = (printer, options) =>
                createConfig(printer, {...labelOptions, ...options});
            try {
                return await qzDispatcher(action, env);
            } finally {
                qz.configs.create = createConfig;
            }
        },
        {force: true}
    );
}
