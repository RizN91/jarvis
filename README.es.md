<div align="center">

<img src="docs/images/hero.webp" alt="Jarvis — a floating glass pill that shows what you said and what it's doing" width="820">

# Jarvis

**Habla con tu PC. Te escucha, te responde y escribe por ti.**

Mantén pulsada una tecla, habla y suéltala: tus palabras aparecen en el cursor,
en la aplicación que tengas delante. O abre una conversación de voz que además
*puede hacer cosas*: buscar en la web, abrir tus aplicaciones, ejecutar tareas
en tu propia máquina.

Sin suscripción. Usa tu propia clave de OpenAI. **Desde $0.0045 por minuto.**

[![Licencia: MIT](https://img.shields.io/badge/License-MIT-3b82f6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3b82f6.svg)](pyproject.toml)
[![Plataforma: Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-3b82f6.svg)](#platform-support)
[![Pruebas: más de 480 superadas](https://img.shields.io/badge/tests-489%20passing-22c55e.svg)](CONTRIBUTING.md)
[![Idiomas: 15](https://img.shields.io/badge/languages-15-8b5cf6.svg)](#speaks-your-language)

**Leer en:** [English](README.md) · **Español** · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

</div>

---

## Míralo funcionar

[![Jarvis demo](docs/media/jarvis-preview.webp)](docs/media/jarvis-demo.mp4)

**▶ Ver la demo de 81 segundos con sonido** · 1080p, 11 MB

---

## Por qué

Piensas más rápido de lo que escribes. Y repites lo mismo todo el día: el mismo
tipo de correo, el mismo mensaje de commit, la misma petición de «resúmeme este
documento».

Las herramientas de dictado resuelven la mitad del problema, y o son una
suscripción, o un juguete, o pulsan Enter a escondidas y te envían el mensaje a
medio escribir.

Jarvis hace las dos mitades, cuesta céntimos y no se interpone. No tiene
suscripción, ni cuenta, ni telemetría, ni servidor propio.

<table>
<tr>
<td width="50%">

**Lo dices, y queda escrito**
> *«Hola Sara: te escribo por la factura del martes, ¿me confirmas el número
> de pedido para poder tramitarla hoy?»*

…acaba en tu correo, tu editor, tu ticket, allí donde esté el cursor.

</td>
<td width="50%">

**O pides algo**
> *«Jarvis, ¿cómo va el lanzamiento de Vue 3.6?»*

…busca, y responde en voz alta, en una conversación que puedes interrumpir.

</td>
</tr>
</table>

## Capturas de pantalla

<div align="center">

<img src="docs/images/pill-states.webp" alt="The Jarvis pill in all six states: idle, dictating, working, listening, speaking, error" width="900">

<sub>La píldora en sus seis estados: inactiva, dictando, trabajando, escuchando, hablando, error.
Nunca roba el foco, y se queda inactiva cuando no pasa nada.</sub>

<br><br>

<img src="docs/images/settings-dark.webp" alt="Jarvis setup wizard, dark theme" width="880">

<sub>Asistente de configuración en doce pasos: comprueba tu micrófono, tus
altavoces, tus atajos y tu clave antes de que dependas de él.</sub>

<br><br>

<img src="docs/images/settings-light.webp" alt="Jarvis settings, light theme" width="880">

<sub>Todas las pantallas tienen tema claro, y toda la interfaz está traducida:
mira más abajo.</sub>

</div>

## Instalación

**Windows 10 u 11.** Necesitas Python 3.11+ y una clave de API de OpenAI.

```bat
git clone https://github.com/RizN91/jarvis.git
cd jarvis
setup.cmd
```

`setup.cmd` busca un Python adecuado, crea un entorno virtual, instala las
dependencias fijadas y ejecuta una comprobación rápida. Después:

```bat
run.cmd
```

El primer arranque abre el asistente de configuración. Te guía por el
micrófono, los altavoces, los atajos, la palabra de activación y el
presupuesto, y te pide tu clave de API, que se guarda en el **Administrador de
credenciales de Windows**, nunca en un archivo.

No se envía nada a ningún sitio hasta que termines el asistente y empieces a
dictar, salvo las pruebas de conexión que decidas ejecutar tú.

¿Prefieres hacerlo a mano?

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m jarvis
```

## Lo que cuesta

Usas tu propia clave de OpenAI, así que le pagas directamente a OpenAI: sin
recargo, sin suscripción, sin licencia por puesto. Dos motores, y eliges tú:

| Motor | Modelo | Coste | Se siente como |
| --- | --- | --- | --- |
| **Economy** *(por defecto para el dictado)* | `gpt-transcribe` | **$0.0045 / min** | unas **27 horas por dólar**; el más preciso para texto literal |
| **Live** | `gpt-live-1` | **$0.05 / min** | unos 20 minutos por dólar; conversacional, maneja interrupciones y turnos |

Para hacerte una idea: **veinte minutos de dictado al día, cinco días a la
semana, cuestan unos 45 centavos al mes** con Economy. El asistente es la parte
cara, y solo mientras hablas con él de verdad.

Jarvis además mide su propio gasto y se detiene en los topes diarios y
mensuales que tú fijes. Esos topes son contadores propios de Jarvis: no puede
leer el límite de tu cuenta de OpenAI, y nunca dice que lo haga.

## Funciones

**Dictado**
- Escribe en **cualquier** aplicación: editores, navegadores, chats, terminales,
  sistemas de tickets.
- **Nunca pulsa Enter**, así que no puede enviar un mensaje a medias.
- Rechaza los campos de contraseña; en las terminales retiene el texto para que
  lo revises en lugar de escribir directamente en una shell.
- Tu propio **vocabulario** —nombres, jerga, nombres de productos—, que se pasa
  al transcriptor como pistas y mejora la precisión de forma medible.
- Deshacer que no se come lo que has escrito tú.

**Asistente de voz**
- `Ctrl+Alt+Space` abre una conversación con interrupción real: habla por encima
  y se calla.
- Puede buscar en la web y actuar en tu máquina a través de una capa de
  herramientas donde cada acción necesita **tu Sí explícito**, con los argumentos
  exactos a la vista.
- Se pueden encargar tareas largas a agentes de código locales (Codex, Claude
  Code), y Jarvis informa de cuál ha usado.

**Siempre**
- **Palabras de activación locales** — «Hey Jarvis», «Hey GPT». Se ejecutan en tu
  máquina (`sherpa-onnx`); **no sale audio del dispositivo mientras escucha** la
  frase de activación.
- **La píldora.** Una superposición flotante con alfa por píxel que muestra lo
  que ha oído y lo que está haciendo, y que de verdad deja pasar los clics sin
  activarse.
- **Privacidad por diseño** — sin telemetría, sin base de datos en la nube, sin
  analítica, sin cuenta. El audio original no se almacena.
- Temas claro y oscuro. Compatibilidad con movimiento reducido.

## Habla tu idioma

Toda la configuración inicial y la interfaz de ajustes vienen en **15 idiomas**,
con un selector en Ajustes:

`English` · `Español` · `Français` · `Deutsch` · `Italiano` · `Nederlands` ·
`Polski` · `Português (BR)` · `Русский` · `Türkçe` · `العربية` (RTL) ·
`हिन्दी` · `中文 (简体)` · `日本語` · `한국어`

Tu idioma se detecta del sistema en el primer arranque. ¿Falta alguno? Es un
archivo y una pull request: consulta [CONTRIBUTING.md](CONTRIBUTING.md).

## Compatibilidad de plataformas

**Windows 10 y 11 son totalmente compatibles.** El núcleo está construido sobre
Win32 —hooks globales de entrada, inserción de texto con `SendInput`, ventanas
por capas para la píldora, DPAPI para la clave, WTS para detectar el bloqueo y
la suspensión—. Eso es lo que hace que se sienta nativo, y también la razón de
que todavía no sea portable.

**macOS y Linux aún no son compatibles.** Ejecutarlo allí termina con un mensaje
claro en vez de un traceback. El port está en la hoja de ruta; es un proyecto de
verdad, no una bandera.

## Hoja de ruta

Lo que viene a continuación, más o menos en orden:

- [ ] **Más modelos, no solo OpenAI** — Claude y Gemini como cerebro del
      asistente, y modelos locales vía Ollama para un modo totalmente sin
      conexión.
- [ ] **Mejores voces** y un selector de voz, incluido tu propio perfil de voz.
- [ ] **Temas y máscaras** — más que claro/oscuro, y una píldora que combine con
      tu escritorio.
- [ ] **Dictado realmente en streaming** — hoy Live graba y luego reproduce, así
      que el tiempo entre soltar y ver el resultado incluye la duración de lo que
      has dicho.
- [ ] **Port a macOS** (Accessibility API + Quartz) y **Linux** (X11).
- [ ] **Complementos** — que la gente pueda añadir sus propios comandos de voz y
      herramientas.
- [ ] **Un instalador firmado**, para que SmartScreen deje de avisar.

Las ideas, los votos y las quejas son bienvenidos en
[Issues](https://github.com/RizN91/jarvis/issues): la hoja de ruta sigue lo que
la gente pide de verdad.

## Cómo funciona

Una versión corta; el detalle de verdad está en [ARCHITECTURE.md](ARCHITECTURE.md).

- Un proceso mínimo en la bandeja del sistema se encarga de los hooks globales,
  el audio y la píldora. En reposo consume **2 procesos, ~53 MB y ~0,3 % de un
  núcleo**.
- La ventana de ajustes es un proceso **aparte**, así que WebView2 (~440 MB) solo
  existe mientras la tienes abierta.
- La voz va directa a OpenAI por WebSocket. **No hay servidor de Jarvis ni
  puerto en localhost** — nada que atacar, nada que se caiga.
- Tus datos (historial, vocabulario, gasto) son SQLite en tu propio disco. No se
  sincroniza nada a ningún sitio.

## Seguridad y privacidad

- Tu clave de API se guarda **solo** en el Administrador de credenciales de
  Windows, envuelta con DPAPI, y nunca se escribe en un archivo de configuración,
  una base de datos, un log, la línea de comandos ni la interfaz.
- Un filtro de redacción borra las cadenas con forma de clave de todos los
  registros del log.
- **Sin telemetría. Sin analítica. Sin cuenta.**
- El audio original no se escribe en disco.
- El arranque al iniciar sesión es opcional y reversible; la desinstalación lo
  elimina todo.

Consulta [SECURITY.md](SECURITY.md) para saber cómo informar de una
vulnerabilidad.

## Contribuir

Los informes de errores, las traducciones, los temas y los nuevos verbos de
herramientas son bienvenidos: empieza por [CONTRIBUTING.md](CONTRIBUTING.md).
Explica las suites de pruebas, cómo añadir un idioma y el puñado de reglas que ya
han evitado errores reales (nada de secretos en los logs, no sintetizar nunca
Enter, no dejar jamás que el texto dictado se convierta en instrucciones).

La suite de pruebas son unas 486 aserciones repartidas en 13 suites gratuitas, y
escribe en aplicaciones reales en vez de en mocks. Hay mucho que construir si
quieres ayudar.

## Licencia

[MIT](LICENSE): haz lo que quieras con él.

Jarvis no está afiliado a OpenAI, ni cuenta con su respaldo ni su patrocinio. Tú
aportas tu propia clave de API y eres responsable de su uso.

---

<div align="center">

**Si Jarvis te ahorra tiempo, una ⭐ ayuda a que otras personas lo encuentren.**

</div>
