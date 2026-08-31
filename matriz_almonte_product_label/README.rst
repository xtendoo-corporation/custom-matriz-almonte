=====================================================
Matriz Almonte - Etiquetas de producto Brother QL-700
=====================================================

Este módulo permite imprimir etiquetas de producto con el tamaño exacto
soportado por la impresora de etiquetas **Brother QL-700**: 5 cm de ancho
(horizontal) por 2,9 cm de alto (vertical).

En cada etiqueta se muestra:

* Referencia interna del producto.
* Nombre del producto.
* Precio de venta con IVA incluido.
* Código de barras (Code128) generado a partir de la referencia interna
  del producto.

Uso
===

1. Ir a la ficha de un producto (o seleccionar varios desde la vista de
   lista de productos o variantes).
2. Pulsar el botón **Etiqueta QL-700** (en la ficha) o usar la acción
   **Imprimir etiquetas (Brother QL-700)** desde el menú de acciones
   (⚙) de la vista de lista.
3. Indicar el número de etiquetas a imprimir por producto y pulsar
   **Imprimir**.
4. Se generará un PDF con una página por etiqueta, con el tamaño exacto
   de 5 x 2,9 cm, listo para enviar a la impresora Brother QL-700.

Configuración de la impresora
==============================

Para obtener el resultado esperado, configure el controlador de
impresión (CUPS / driver de Brother) con un tamaño de página personalizado
de 50 mm x 29 mm, sin márgenes, y desactive el "ajustar a página" para
que el PDF se imprima a tamaño real (100%).

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

Si ese tamaño de página no es EXACTAMENTE 50 x 29 mm, el PDF (que sí
declara correctamente 50 x 29 mm) se "estampa" dentro de una página de
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
   **50 mm x 29 mm** (ancho x alto). Guardarlo con un nombre
   identificable (p. ej. "Etiqueta 50x29").
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
   diálogo del navegador verificar: tamaño de papel = 50 x 29 mm (o el
   nombre dado al tamaño personalizado), escala = **100% / Tamaño real**
   (nunca "Ajustar al tamaño del papel"), márgenes = **Ninguno**.

**Prueba de diagnóstico:** imprima el mismo PDF generado por Odoo
directamente con "Microsoft Print to PDF" o ábralo con un lector y
compruebe que el contenido ocupa el 100% de la página de 50 x 29 mm (si
es así, el PDF está bien y el ajuste pendiente es únicamente el del
driver/cola de impresión descrito arriba).

Si en cambio existe una integración por script que llama a
``qz.print()`` directamente (sin pasar por el diálogo del navegador),
indique también de forma explícita el tamaño, las unidades y desactive
el escalado::

    var config = qz.configs.create("Brother QL-700", {
        units: "mm",
        size: {width: 50, height: 29},
        scaleContent: false,
        rasterize: false,
        density: 300,
        orientation: "portrait",
        margins: 0
    });
    qz.print(config, [
        {type: "pixel", format: "pdf", flavor: "base64", data: pdfBase64}
    ]);


