# ADR 0008: el formulario 210 se lee por coordenadas del flujo de contenido

Fecha: 2026-07-26. Estado: aceptado.

## Contexto

La declaracion presentada del ano anterior y el borrador sugerido del ano en curso son el
mismo formulario 210, y son los dos documentos con mas valor del expediente: de ahi salen el
patrimonio y los ingresos con que se verifica lo que el cliente declara este ano. Mientras no
tuvieran lector, la consola los mostraba como documentos que no se pudieron leer.

El PDF que entrega el portal no tiene `AcroForm` y, a diferencia del RUT, **los numeros de
casilla son parte de una imagen de fondo**: no existen como texto. No hay ninguna etiqueta
que permita pedir "la casilla 29"; lo unico que distingue una casilla de otra es donde esta.

## Decision

Ubicar cada valor por su posicion, leyendo los operadores `Tm` directamente del flujo de
contenido del PDF (`page.get_contents()`), no con el extractor de texto de la libreria.

La coordenada vertical identifica la fila del formulario y la horizontal la columna. El mapa
de filas (`_ROWS`) es declarativo y cada fila dice que juego de columnas usa, porque el
formulario no tiene una sola reticula: la franja del patrimonio, la cedula general y la
franja de totales tienen anchos distintos. Los valores se imprimen alineados a la derecha, de
modo que la coordenada de inicio depende de cuantos digitos tenga el numero; por eso cada
columna es una banda y no una posicion.

## Por que no el extractor de texto de la libreria

Fue el primer intento y produjo un parser que funcionaba por accidente. `extract_text` con un
`visitor` agrupa trazos y no reporta la matriz de posicion de todos: muchos valores llegaban
con coordenada `(0, 0)`. El parser interpretaba esos como "continuacion" del valor anterior y
los repartia por orden de aparicion. Daba el resultado correcto, pero apoyado en un detalle de
implementacion del extractor y no en el documento: la casilla 30 (Deudas), por ejemplo, se
dibuja en `x=366`, que no cae en ninguna de las columnas de la cedula general, y solo se leia
bien porque llegaba sin coordenadas.

Leer el flujo directamente da la coordenada exacta de cada valor. El precio es depender de
que el generador de la DIAN emita `Tm` por valor (hoy: los 215 valores de la pagina). Si
cambiara a movimientos relativos, el parser no leeria nada y lo diria con un aviso, que es la
forma correcta de fallar.

## Como se calibro

Rasterizando el PDF y transcribiendo los numeros de casilla impresos, casilla por casilla, no
deduciendolos del orden en que aparecen los valores. El primer intento se hizo deduciendo y
dejo el mapa incompleto (faltaban siete filas) y con casillas mal atribuidas, sin que nada lo
delatara: los numeros leidos eran verosimiles.

La verificacion compara las 71 casillas contra esa transcripcion, no contra la salida del
propio parser.

## Alcance deliberadamente parcial

Se mapean el patrimonio y la cedula general completa (casillas 28 a 98). Las cedulas de
pensiones y de dividendos, y las ganancias ocasionales, no estan mapeadas. Un mapa incompleto
es honesto; un mapa adivinado es peligroso, porque un valor puesto en la casilla equivocada
corrompe justo la validacion que protege al declarante.

## Tres defensas contra el modo de falla propio de un parser posicional

El peor final de un parser como este no es fallar: es entregar el valor de una casilla en el
lugar de otra y que nadie se entere.

1. **Identidades del formulario.** El formulario se valida solo: el patrimonio liquido es el
   bruto menos las deudas, la renta liquida es los ingresos menos lo no constitutivo, la renta
   liquida de la cedula general es la suma de las cuatro columnas. Se comprueban nueve
   identidades y una diferencia mayor al redondeo al millar emite `FORM_ARITHMETIC_MISMATCH`.
2. **Celdas sombreadas.** El formulario deja en blanco las celdas que no aplican (las
   devoluciones y descuentos solo existen para rentas no laborales). El mapa las declara como
   `None`, asi que un valor que caiga en una de ellas significa que el mapa no corresponde a
   esta version del formulario y emite `FORM_LAYOUT_NOT_RECOGNIZED`.
3. **Confianza baja.** Todos los montos se marcan `Confidence.LOW` y llevan el nombre impreso
   de su casilla como procedencia, para que ningun consumidor tenga que saber que es "la
   casilla 42" y para que quien los use sepa que conviene confirmarlos.

## Efecto lateral que salio gratis

La cabecera imprime el ano gravable y la cedula del declarante digito por digito, en casillas
individuales. Recomponerlos por posicion hace que la declaracion entre al cruce de identidad
que ya existia (`DOCUMENT_IDENTITY_MISMATCH`): subir la declaracion de otra persona, que es
un error facil para quien maneja varias, ahora frena el expediente.
