# handctl

[![tests](https://github.com/humodev/gesture_cam/actions/workflows/tests.yml/badge.svg)](https://github.com/humodev/gesture_cam/actions/workflows/tests.yml)

Controlá la computadora con las manos usando la webcam: mouse, menú de
gestos, dictado por voz, pizarrón para escribir sobre la pantalla e
instrumentos musicales. Hand tracking con MediaPipe, todo local (nada sale de
tu computadora). Funciona en **Linux, Windows y macOS**.

## Instalación

Necesitás una webcam y [uv](https://docs.astral.sh/uv/getting-started/installation/)
(instala Python 3.12 solo). Después, en cualquier sistema:

```sh
git clone https://github.com/humodev/gesture_cam.git
cd gesture_cam
uv sync
uv run handctl --calibrate   # la primera vez: ajusta la zona y el pellizco a tu mano
```

La primera vez descarga el modelo de manos (~8 MB) y, si usás dictado, Whisper (~1.6 GB).

### 🐧 Linux

- Probado en KDE Plasma (Wayland). El mouse y el teclado se inyectan por
  `/dev/uinput`, así que funciona en Wayland y X11. Tu usuario necesita
  permiso de escritura en `/dev/uinput`; si no lo tiene:
  `sudo setfacl -m u:$USER:rw /dev/uinput` (o una regla de udev para que sea permanente).
- El dictado pega por el portapapeles de KDE (Klipper); en otros escritorios
  instalá `wl-clipboard` (Wayland) o `xclip` (X11).
- Los atajos de escritorios y vista general son los de KDE; en GNOME u otros,
  cambialos en `config.toml` (ver Configuración).
- Dictado en GPU con NVIDIA (CUDA); sin NVIDIA: `voice_device = "cpu"` y `voice_model = "small"`.

### 🪟 Windows (10/11)

- No hace falta nada extra: el mouse y el teclado se manejan con la API de
  Windows (`SendInput`).
- La primera vez Windows pide permiso para usar la cámara y el micrófono.
- No puede controlar ventanas de programas abiertos **como administrador**
  (limitación de Windows), salvo que handctl también corra como administrador.
- Dictado en GPU con NVIDIA (CUDA); sin NVIDIA: `voice_device = "cpu"` y `voice_model = "small"`.
- Gestos de sistema: Win+Tab (vista de tareas), Ctrl+Win+←/→ (escritorios), Win+D.

### 🍎 macOS (Apple Silicon: M1 o posterior)

- Las Mac con Intel **no** están soportadas: MediaPipe no publica versión para ellas.
- Dar permisos en **Ajustes del Sistema → Privacidad y seguridad** a la app
  desde la que lo corrés (Terminal, iTerm…):
  - **Cámara** y **Micrófono** (los pide la primera vez),
  - **Accesibilidad** (para mover el mouse y escribir; hay que agregarla a mano).
  Después de darlos, cerrá y volvé a abrir la terminal.
- Copiar/pegar/deshacer usan ⌘; el teclado en pantalla tiene ⌘ Cmd en vez de Ctrl.
- Dictado en CPU con el modelo `small` (no hay CUDA en Mac).
- Gestos de sistema: Ctrl+↑ (Mission Control), Ctrl+←/→ (Spaces), F11 (escritorio).

## Uso

```sh
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

#### ✎ Pizarrón

Para escribir o marcar sobre la pantalla (por ejemplo, al compartir pantalla).
El fondo es transparente: se ve lo que hay debajo.

- **Dibujar:** ☝ señalá con el índice (los otros dedos cerrados), o pellizcá
  índice y pulgar. **Mano abierta** = levantar el lápiz.
- **Herramientas** (barra a la derecha, índice quieto ½ s encima): colores,
  grosor, lápiz, resaltador, **láser** (trazo que se borra solo), borrador,
  deshacer, borrar todo, fondo (transparente / oscuro / blanco) y 💾 guardar
  como PNG en `~/Imágenes`.
- Los trazos quedan mientras handctl esté abierto, aunque cierres la app.
- La cámara en miniatura de la esquina también sale al compartir pantalla:
  ocultala desde el ícono de la bandeja si molesta.

#### Instrumentos

Todo el sonido lo genera handctl (sin samples ni programas externos). Los
botones de los instrumentos se activan **dejando la mano quieta encima** un
instante, sin pellizcar.

| App | Cómo se toca |
|---|---|
| 🥁 **Octapad** | 8 pads que se tocan **golpeando hacia abajo** en el aire, como un tambor: suena el pad donde la mano frena, más rápido = más fuerte. Kits: Batería, Electrónica, Percusión, Marimba, Acordes, Guitarra. |
| 〰 **Theremín** | Izquierda↔derecha = nota (Do3–Do5), altura = volumen (abajo de todo, silencio). Cada mano es una voz. Pellizco índice: afinado a la escala ↔ glissando libre. |
| ◈ **Sinte XY** | Pellizco índice = suena un arpegio. ← → acorde, ↑ ↓ brillo. Altura de la otra mano = velocidad. Pellizco medio = octava arriba. |
| ≋ **Cuerdas en el aire** | Pasá la punta del índice o medio por una cuerda para tocarla (más rápido = más fuerte). Pellizco índice en una punta: estirar/achicar (más larga = más grave); en el centro: mover. Crear: pellizco medio y arrastrar, o pellizco índice con las dos manos y estirar. Achicarla hasta casi nada la borra. |
| 🔁 **Estudio (looper)** | Barra arriba: elegís instrumento (abajo se toca como siempre), **● Grabar** (cuenta 1 compás con metrónomo y graba una vuelta; cada grabación se suma como capa), ▶/■, ↶ deshacer capa, ✕ borrar, metrónomo, tempo −/+ y largo (1, 2 o 4 compases). El bucle sigue sonando aunque cambies de instrumento o cierres el estudio. Máximo 16 capas. |

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

Tus valores van en `~/.config/handctl/config.toml` (en Windows:
`C:\Users\<vos>\.config\handctl\config.toml`). `--calibrate` escribe ahí
la zona y los umbrales de pellizco. Ejemplo:

```toml
dominant_hand = "left"
precision_gain = 0.5          # 1.0 = sin modo precisión
scroll_invert = true
voice_paste = "ctrl+shift+v"  # si dictás sobre todo en la terminal
swipe_up = "meta+tab"
overlay_screen = "HDMI-A-1"   # monitor del overlay (vacío = principal)
overlay_preview_width = 0     # ocultar la cámara en miniatura
launcher_items = ["copiar", "pegar", "deshacer", "play_pausa", "volumen", "dictar", "apps", "Reabrir=primary+shift+t"]
# GNOME (Linux): swipe_up = "super", swipe_left = "super+pageup", swipe_right = "super+pagedown"
```

En los atajos, `primary` es Ctrl en Linux y Windows y ⌘ en Mac. También
existen `ctrl`, `shift`, `alt` (`option`), `meta` (`win`, `cmd`, `super`).

Todas las claves, con su explicación, están en `src/handctl/config.py`.

## Pruebas

```sh
uv run python tests/test_plataformas.py   # backends de mouse/teclado de Linux, Windows y Mac (sin eventos reales)
uv run python tests/test_gestos.py        # lógica de gestos con manos sintéticas (sin cámara)
uv run python tests/test_instrumentos.py  # cada instrumento abierto desde el menú, sin sonido
```

## Limitaciones

- El teclado en pantalla manda teclas físicas: en Linux y Windows siguen tu
  layout de teclado; no tiene `ñ` ni tildes (el dictado sí las escribe).
- El dictado pega con Ctrl+V (⌘V en Mac) por el portapapeles y después lo
  restaura. En terminales de Linux/Windows hace falta `voice_paste = "ctrl+shift+v"`.
- Probado a fondo en Linux (KDE Plasma). Windows y macOS pasan las pruebas
  automáticas, pero todavía no se probaron con manos reales.
- Necesita buena luz. Con poca luz aumentan el desenfoque y los clics falsos.
- Si las manos se cruzan, se intercambian los roles.
- Con varios monitores, el cursor recorre el escritorio completo (todos los
  monitores). El overlay se muestra en uno solo.
