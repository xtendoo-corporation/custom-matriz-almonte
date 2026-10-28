=====================================================
Matriz Almonte - Etiquetas de producto Brother QL-700
=====================================================

Este módulo permite imprimir etiquetas de producto para la impresora de
etiquetas **Brother QL-700** con cinta continua de **29 mm** (p. ej. DK-22210).
La etiqueta se genera en **50 x 25 mm** (horizontal): es el área que la QL-700
puede imprimir de verdad en esa cinta (el driver deja ~1,5 mm de margen por
lado y ~3 mm en los extremos), de modo que el PDF no hace falta reescalarlo.

En cada etiqueta, todo centrado, se muestra:

* Nombre del producto.
* Precio de venta con IVA incluido.
* Código de barras (Code128) generado a partir de la referencia interna
  del producto. Se dibuja en vectorial (SVG), con barras de múltiplos
  exactos de los 300 dpi de la impresora, para que salgan nítidas y el
  lector las lea a la primera.
* Referencia interna del producto.

Uso
===

1. Ir a la ficha de un producto (o seleccionar varios desde la vista de
   lista de productos o variantes).
2. Pulsar el botón **Etiqueta QL-700** (en la ficha) o usar la acción
   **Imprimir etiquetas (Brother QL-700)** desde el menú de acciones
   (⚙) de la vista de lista.
3. Indicar el número de etiquetas a imprimir por producto y pulsar
   **Imprimir**.
4. Se generará un PDF con una página por etiqueta (50 x 25 mm), listo
   para enviar a la impresora Brother QL-700.

Configuración de la impresora
==============================

La QL-700 necesita un papel personalizado **29 mm de ancho x 56 mm de
largo** (50 mm de etiqueta + los ~3 mm de margen de cada extremo, que
el driver no imprime), tipo de soporte **cinta continua** (``Tape``) y
escala **100 % / tamaño real** (nunca "ajustar a la página").

Probado en Linux (CUPS, driver ``ptouch-ql``)::

    lpoptions -p QL-700 -o PageSize=Custom.29x56mm -o MediaType=Tape -o CutLabel=1
    lp -d QL-700 -o print-scaling=none etiqueta.pdf

**Corte:** activar el corte automático y cortar **tras cada etiqueta**
(``CutLabel=1``). Con el valor por defecto (0) la impresora solo corta al
final del trabajo y varias etiquetas salen pegadas en una sola tira.

En Windows, en *Preferencias de impresión* del driver Brother, crear un
tamaño personalizado de 29 x 56 mm (cinta continua) y comprobar la prueba
de diagnóstico de abajo. Con un tamaño de papel de 50 x 29 mm la etiqueta
sale reducida y el código de barras sale dentado.

Configuración con QZ Tray
=========================

El módulo depende de ``base_report_to_printer_qztray``. Ese módulo manda el
PDF a QZ Tray **sin indicar tamaño de papel**, así que QZ imprime en el papel
por defecto de la cola/driver y reescala la etiqueta (sale pequeña y
descentrada). Para evitarlo, ``static/src/js/qz_label_print.esm.js`` envuelve
el dispatcher de QZ y, **solo para los dos informes de etiquetas**, pasa a
QZ papel de 56 x 29 mm, sin escalado, márgenes ``top 3 / left 2`` mm y 300 dpi.
Así no hay que configurar el papel por defecto en cada equipo.

Requisitos en el equipo que imprime:

1. QZ Tray en marcha y la impresora de Odoo (*Ajustes > Imprimiendo >
   Impresoras*) con **el mismo nombre** que la cola o el driver local
   (QZ busca por nombre): en Windows suele ser ``Brother QL-700``; en Linux
   CUPS no admite espacios, p. ej. ``QL-700``.
2. El informe de etiquetas con acción ``Send to Printer`` (por defecto o
   como acción específica del usuario).
3. Soporte continuo de 29 mm y el corte automático tras cada etiqueta
   (``CutLabel=1`` en CUPS; en Windows, *Preferencias de impresión*).

Los márgenes se calibraron en Linux/CUPS con ``ptouch-ql``. Si en el driver de
Windows el contenido sale desplazado, hay que ajustar ``margins`` en
``LABEL_QZ_OPTIONS``.

Si en cambio existe una integración por script que llama a
``qz.print()`` directamente (sin pasar por el diálogo del navegador),
indique también de forma explícita el tamaño, las unidades y desactive
el escalado::

    var config = qz.configs.create("Brother QL-700", {
        units: "mm",
        size: {width: 29, height: 56},
        scaleContent: false,
        rasterize: false,
        density: 300,
        orientation: "portrait",
        margins: 0
    });
    qz.print(config, [
        {type: "pixel", format: "pdf", flavor: "base64", data: pdfBase64}
    ]);


