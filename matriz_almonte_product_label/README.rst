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

Si se imprime con `QZ Tray <https://qz.io/>`_ usando la app/extensión
que **sustituye el diálogo de impresión del navegador** (el caso más
habitual: no hay ningún script que llame a ``qz.print()`` con una
configuración propia, simplemente se imprime el PDF como siempre y
QZ Tray reenvía el trabajo a la impresora física), el tamaño de página
y la escala **NO** los controla QZ Tray, sino:

1. El **driver de Windows** de la Brother QL-700 (el tamaño de "media"/
   papel configurado para esa cola de impresión).
2. Las opciones elegidas en el **diálogo de impresión del navegador**
   (tamaño de papel, escala, márgenes) en el momento de imprimir.

Si ese tamaño de página no es el indicado arriba (29 x 56 mm), el PDF (que sí
declara correctamente 50 x 25 mm) se "estampa" dentro de una página de
otro tamaño (p. ej. Carta/A4 o una plantilla Brother distinta como el
rollo continuo DK-22205 de 62 mm), y el resultado impreso se ve
reducido y desplazado hacia una esquina/lado, aunque el PDF en sí sea
correcto. **Esto ocurre igual en las dos etiquetas** porque ambas
comparten el mismo tamaño de página (``paperformat_brother_ql700``): el
problema no está en el contenido de cada plantilla, sino en la
configuración de impresión.

**Pasos a seguir en Windows:**

1. *Panel de control > Dispositivos e impresoras* → clic derecho sobre
   **Brother QL-700** → **Preferencias de impresión**.
2. En la pestaña **Básico**, en "Tamaño de papel", elegir
   **Definido por el usuario / Custom** y crear un tamaño nuevo de
   **29 mm x 56 mm** (ancho x alto). Guardarlo con un nombre
   identificable (p. ej. "Etiqueta 29x56").
3. Seleccionar ese tamaño personalizado y pulsar **Aceptar**. Es
   importante hacerlo desde *Dispositivos e impresoras* (propiedades del
   documento predeterminado del driver), no solo desde el diálogo de
   impresión de una aplicación, para que quede como valor por defecto
   de la cola y lo respete también QZ Tray.
4. Desactivar cualquier casilla de "Ajustar al papel" / "Auto cortar
   con margen extra" / "Escalar" que fuerce un tamaño distinto.
5. Si se dispone de la **Brother Printer Setting Tool**, se puede crear
   /verificar el mismo tamaño personalizado desde ahí también.
6. Al imprimir el PDF (Ctrl+P o el botón de imprimir de QZ Tray), en el
   diálogo del navegador verificar: tamaño de papel = 29 x 56 mm (o el
   nombre dado al tamaño personalizado), escala = **100% / Tamaño real**
   (nunca "Ajustar al tamaño del papel"), márgenes = **Ninguno**.

**Prueba de diagnóstico:** imprima el mismo PDF generado por Odoo
directamente con "Microsoft Print to PDF" o ábralo con un lector y
compruebe que el contenido ocupa el 100% de la página de 50 x 25 mm (si
es así, el PDF está bien y el ajuste pendiente es únicamente el del
driver/cola de impresión descrito arriba).

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


