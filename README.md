# handctl

Controlá el mouse, escribí y dictá con las manos usando la webcam. Hand
tracking con MediaPipe, entrada vía `/dev/uinput` (funciona en Wayland/KDE) y
dictado con Whisper local en la GPU.

## Uso

```fish
uv run handctl --calibrate  # la primera vez: ajusta zona y pellizco a tu mano
uv run handctl              # control real
uv run handctl --dry-run    # practicar sin mover nada
uv run handctl --no-voice   # sin dictado (no carga Whisper)
uv run handctl --window     # ventana de vista previa clásica en vez del overlay
```

Por defecto todo se ve en un **overlay**: una capa transparente encima de la
pantalla que no recibe foco ni clics. Muestra un aro de pellizco alrededor del
cursor, el menú, el teclado grande abajo, el estado del dictado y la cámara en
miniatura arriba a la derecha.

Salir o pausar: ícono de handctl en la bandeja del sistema, o `Ctrl+C` en la terminal.

## Una mano, la otra o las dos

Funciona con **cualquier mano sola**, izquierda o derecha. Para descansar una,
bajala o sacala de la zona y la otra sigue sola.

- Solo cuentan las manos **en juego**, es decir, visibles en casi toda la imagen.
  Para descansar una, **bajala al borde inferior** de la imagen o sacala de
  cuadro: queda **en reposo**, se dibuja gris y no hace nada.
- El rectángulo celeste de la miniatura es otra cosa: la zona de la imagen que
  se mapea a la pantalla completa (la ajusta `--calibrate`).
- Si hay dos en juego, la que ya tenía el cursor lo conserva y la otra **suma**
  gestos (clics, scroll, deslizar). Pellizcar con la otra no mueve el cursor,
  así que es lo más preciso.
- Si la mano del cursor se va a descansar, la otra toma el cursor sin saltos.
- Si aparecen las dos a la vez, el cursor lo toma `dominant_hand`.

## Gestos

| Gesto | Modo mouse | Modo teclado |
|---|---|---|
| mano abierta, mover | mover el cursor | mover tu cursor sobre el teclado |
| pellizco pulgar + índice | clic; mantener = arrastrar | pulsar tecla (⌫ y flechas se repiten) |
| pellizco pulgar + medio | clic derecho | borrar |
| pellizco pulgar + anular (mantener) | **dictar**: hablá y soltá | dictar |
| mano en **V** + subir/bajar | scroll suave con inercia | — |
| ✊ **puño quieto** (mantener ½ s) | abrir el **menú** | cerrar teclado |
| 👍 **pulgar arriba** (mantener ~1 s) | pausa / reanudar | pausa / reanudar |
| mano abierta, deslizar rápido (la que **no** tiene el cursor) | ← → escritorio · ↑ vista general · ↓ escritorio | — |

- Los pellizcos cuentan si los otros dedos no están cerrados (así, cerrar el
  puño no hace clic).
- Puño y pulgar arriba cuentan con la mano **quieta**: si la mano se desplaza
  mientras los hacés (agarrando algo, bajándola a descansar) el conteo vuelve
  a empezar.
- Mientras mantenés puño o pulgar arriba, un aro blanco alrededor del cursor y
  una barra arriba en la pantalla muestran cuánto falta y qué va a pasar.
- En la pantalla, un **aro** alrededor del cursor se llena a medida que acercás
  el pulgar a un dedo. El color indica qué dedo y lleno = pellizco (al lado
  dice "clic", "clic der." o "dictado").
- Deslizar vale si la mano **sigue en la zona** un instante después: bajarla
  a descansar o sacarla de cuadro rápido no cambia de escritorio.

### Menú (lanzador)

✊ **Puño quieto ½ s** en modo mouse abre un menú circular alrededor del
cursor. El cursor queda quieto mientras está abierto.

1. **Abrí la mano** (el punto del centro se pone de color: el menú está listo).
2. **Movela hacia la opción** que querés: se resalta en azul. No hace falta
   un gran recorrido; en el centro no hay nada elegido.
3. **Pellizcá** (pulgar con cualquier dedo) para elegir.

Para cerrarlo sin hacer nada: pellizco en el centro, otro puño, o esperá unos
segundos. Opciones por defecto (en sentido horario desde arriba):

| Opción | Qué hace |
|---|---|
| 📋 Copiar · 📥 Pegar · ↶ Deshacer | `Ctrl+C` · `Ctrl+V` · `Ctrl+Z` |
| ✕ Cerrar pestaña | `Ctrl+W` |
| ⏯ Play / pausa | tecla multimedia (música, videos) |
| 🔊 Volumen | subí o bajá la mano: cada marca es un paso; pellizcá para terminar |
| 🎤 Dictar | dictado **manos libres**: graba hasta que pellizcás (podés bajar las manos mientras hablás) |
| ⌨ Teclado | teclado en pantalla (puño para cerrarlo) |
| 🎵 Apps | lista de apps a pantalla completa (ver abajo) |
| ? Ayuda | tarjeta con todos los gestos |

Se cambian con `launcher_items` en el config (nombres en
`src/handctl/launcher.py`, p.ej. `vista_general`, `escritorio_izq`,
`siguiente`, `silencio`, `guardar`) y se pueden agregar atajos propios como
`"Reabrir=ctrl+shift+t"`.

### Apps

Menú → 🎵 **Apps** → elegí una. La app ocupa la pantalla: mientras está
abierta handctl no mueve el mouse ni escribe, las manos son de la app.
**✊ Puño quieto** la cierra; 👍 pausa como siempre. (Las apps se dibujan en
el overlay, así que con `--window` no están disponibles.)

### Cursor de precisión

Si movés la mano despacio, el cursor se mueve ~1/3 de lo que movés la mano
(para botones chicos). Si la movés rápido, va 1:1 y se re-alinea con la mano.

### Escribir

1. En modo mouse, hacé clic en el campo de texto de destino.
2. Para textos, **dictar**: mantené el pellizco con el anular, hablá y
   soltá; o, para textos largos, Menú → 🎤 **Dictar**, hablá con las manos
   libres y pellizcá para terminar. El texto se pega en la ventana con foco,
   con tildes y ñ. Mientras graba, arriba en la pantalla se ve "grabando" y
   el nivel del micrófono.
3. Para retoques: Menú → ⌨ **Teclado**. Con dos manos hay dos cursores: cada
   mano escribe con su propio pellizco.

Las teclas van a la ventana con foco. El overlay nunca toma el foco, así que
podés escribir en cualquier app que esté debajo.

## Configuración

Tus valores van en `~/.config/handctl/config.toml`. `--calibrate` escribe ahí
la zona y los umbrales de pellizco. Ejemplo:

```toml
dominant_hand = "left"
precision_gain = 0.5          # 1.0 = sin modo precisión
scroll_invert = true
voice_paste = "ctrl+shift+v"  # si dictás sobre todo en la terminal
swipe_up = "meta+tab"
overlay_screen = "HDMI-A-1"   # monitor del overlay (vacío = principal)
overlay_preview_width = 0     # ocultar la cámara en miniatura
launcher_items = ["copiar", "pegar", "deshacer", "play_pausa", "volumen", "dictar", "apps", "Reabrir=ctrl+shift+t"]
```

Todas las claves, con su explicación, están en `src/handctl/config.py`.

## Pruebas

```fish
uv run python tests/test_gestos.py   # lógica de gestos con manos sintéticas (sin cámara)
```

## Limitaciones

- El teclado en pantalla sigue el layout US: sin `ñ` ni tildes (el dictado sí las escribe).
- El dictado pega con `Ctrl+V` vía Klipper y después restaura tu portapapeles.
  En terminales hace falta `voice_paste = "ctrl+shift+v"`.
- Necesita buena luz. Con poca luz aumentan el desenfoque y los clics falsos.
- Si las manos se cruzan, se intercambian los roles.
- Con varios monitores, el cursor recorre el escritorio completo (todos los
  monitores). El overlay se muestra en uno solo.
