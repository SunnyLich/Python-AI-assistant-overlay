<div align="center">

<img src="assets/doll/idle.png" width="112" alt="Icono de Wisp" />

# Wisp

**¿Cansado de escribir los mismos prompts y pegar el mismo contexto?**

Wisp te da IA controlada por atajos de teclado que puede leer tu selección, portapapeles, aplicación, navegador, documentos o captura de pantalla mientras te quedas donde estás. Presiona un atajo, elige una intención y transmite la respuesta en una pequeña superposición.

[![Plataforma](https://img.shields.io/badge/plataforma-Windows%20%7C%20macOS%20%7C%20Linux-333333?style=flat-square)](#estado-de-plataformas)
[![Python](https://img.shields.io/badge/python-3.12-3572A5?style=flat-square)](#inicio-rápido)
[![Local primero](https://img.shields.io/badge/local--first-contexto%20y%20memoria-4B8F8C?style=flat-square)](#privacidad-y-control)
[![Licencia](https://img.shields.io/badge/licencia-MIT-7C3AED?style=flat-square)](#licencia)

**Idiomas:** [English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [Français](README.fr.md) | Español

[Inicio rápido](#inicio-rápido) | [Qué hace](#qué-hace-wisp) | [Demos](#demos) | [Configuración](#configuración) | [APIs gratuitas](#fuentes-de-api-de-modelos-gratuitas) | [Privacidad](#privacidad-y-control)

![Demo Wisp Ctrl+Q](ReadMe%201st%20Demo.gif)

**Consulta en superposición:** Presiona un atajo, elige una intención y obtén una respuesta transmitida sin salir de la aplicación que ya estás usando.
</div>

---

## Qué hace Wisp

Wisp es para los momentos en que abrir una aplicación de chat interrumpiría tu flujo de trabajo.

Selecciona texto, presiona `Ctrl+Q`, pulsa una tecla de intención, y Wisp consulta tu modelo configurado solo con las fuentes de contexto que habilitaste. Las respuestas se transmiten en una burbuja compacta junto al ícono flotante. Si el TTS está habilitado, la respuesta se habla a medida que llega.

| En lugar de... | Wisp te permite... |
| --- | --- |
| Copiar texto en una ventana de chat separada | Preguntar desde la aplicación que ya estás usando |
| Reescribir los mismos prompts una y otra vez | Vincular prompts a atajos y filas de intención |
| Leer respuestas largas cada vez | Escuchar la respuesta mediante TTS en streaming |
| Explicar manualmente lo que está en pantalla | Capturar selección, portapapeles, documentos, páginas del navegador y capturas de pantalla |
| Confiar a un asistente remoto el almacenamiento | Mantener la memoria y la configuración en tu máquina |

## Destacados

- **Superposición primero** — un ícono flotante, selector de intención y burbuja de respuesta se mantienen al frente sin tomar el control de tu escritorio.
- **Privacidad por defecto** — Wisp no tiene capa de almacenamiento alojada; los datos se quedan en tu máquina, y el modo privacidad puede advertir o redigir antes de que el contexto sensible salga.
- **Altamente personalizable** — cada atajo, tecla de intención, prompt, fuente de contexto, comportamiento de pegado, ruta del modelo, configuración de voz y dimensión de burbuja se puede cambiar.
- **Interfaz accesible** — la configuración, las verificaciones de configuración, los informes de privacidad, las herramientas de memoria y las advertencias del modelo explican qué está pasando sin necesidad de leer el código.
- **Captura de contexto** — Wisp puede leer texto seleccionado, texto del portapapeles, UI enfocada, documentos abiertos, contenido del navegador, archivos recientes y capturas de pantalla opcionales.
- **Voz de entrada y salida** — STT local mediante faster-whisper, más Cartesia, ElevenLabs, OpenAI, compatible con OpenAI, o TTS deshabilitado.
- **Capturas visuales** — dibuja una región con `Ctrl+Alt+Q` y envía la captura de pantalla a un modelo de visión.
- **Reescribir y pegar** — usa `Ctrl+Shift+Q` para reescribir el texto seleccionado con el contexto capturado y pegar el resultado de vuelta en el campo activo.
- **Trae tu propio proveedor** — Groq, Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Mistral, XAI, Together, Cerebras, servidores compatibles con OpenAI personalizados, GitHub Copilot, y más.
- **Memoria local** — la memoria a corto y largo plazo opcional se almacena localmente, con un visor para editar o eliminar hechos.
- **Complementos** — extiende Wisp con hooks, acciones de bandeja, configuraciones, herramientas llamables por el modelo, intenciones y atajos.
- **Tareas de agente** — existe un marco de tareas en espacio aislado para trabajos más largos que necesitan descomposición, revisión y artefactos.

## Demos

![Demo de captura de pantalla Wisp Ctrl+Alt+Q](ReadMe%202nd%20Demo.gif)

**Captura visual:** El flujo de captura es para casos donde el contexto visual importa. `Ctrl+Alt+Q` te permite dibujar una región, enviar solo ese recorte a un modelo de visión, y mantener la respuesta en la superposición en lugar de cambiar de aplicación.

![Demo de reescritura contextual de Wisp](ReadMe%203rd%20Demo.gif)

**Reescritura contextual:** Wisp puede recopilar contexto útil de la aplicación sin tomar una captura de pantalla, para que el modelo sepa en qué estás trabajando. Luego `Ctrl+Shift+Q` reescribe solo el texto seleccionado y pega el reemplazo donde estabas trabajando.

![Demo de tarea multi-agente de Wisp](ReadMe%204th%20Demo.gif)

**Ejecución de agente en espacio aislado:** El flujo de tareas de agente es para trabajos de espacio de trabajo más largos. Wisp puede dividir una tarea entre roles de coordinador, constructor y revisor, inspeccionar archivos del proyecto, hacer un cambio enfocado, ejecutar verificaciones, y dejar un informe final y artefactos para la ejecución.

## Flujo de trabajo

```text
seleccionar texto, elegir contexto, o dibujar una captura
  -> presionar el atajo de llamada
  -> Wisp captura solo el contexto seleccionado o habilitado
  -> elegir una intención o escribir un prompt personalizado
  -> enviar directamente a tu proveedor de modelo configurado
  -> transmitir la respuesta del modelo
  -> mostrar burbuja + TTS opcional
  -> opcionalmente almacenar memoria útil localmente
```

Flujos de ejemplo:

| Momento | Acción | Resultado |
| --- | --- | --- |
| Quieres una explicación del texto seleccionado | Selecciona el texto, presiona `Ctrl+Q`, luego elige `W` (¿Qué es esto?) o `A` (Explicar simplemente) | Wisp explica la selección en la superposición |
| Quieres reescribir una oración | Primero selecciona la oración, presiona `Ctrl+Shift+Q`, luego elige `W`, `A` o `D` para gramática, simplificación o tono | Wisp reescribe el texto seleccionado y puede volver a pegarlo |
| Necesitas hacer tu propia pregunta | Presiona `Ctrl+Q`, presiona `S`, escribe el prompt, luego presiona Enter | Wisp envía tu prompt personalizado con cualquier contexto habilitado para ese llamador |
| Un elemento de UI o imagen es confuso | Presiona `Ctrl+Alt+Q`, dibuja un cuadro, luego elige una intención o prompt personalizado | Wisp envía la captura a un modelo de visión |
| Quieres consultar el modelo por voz | Mantén `F9`, habla, luego suéltalo | Wisp transcribe tu voz y la envía como consulta del modelo |
| Quieres dictar en otra aplicación | Mantén `F8`, habla, luego suéltalo | Wisp transcribe tu voz directamente en el campo de texto enfocado |

## Inicio rápido

Hay dos formas compatibles de iniciar Wisp.

### Opción 1: Aplicación empaquetada

Usa esto si quieres la aplicación sin clonar el repositorio o gestionar dependencias de Python.

1. Descarga el último artefacto para tu plataforma desde [GitHub Releases](https://github.com/SunnyLich/Python-AI-assistant-overlay/releases).
2. Descomprime el archivo y inicia la aplicación empaquetada.
3. Abre Configuración para agregar tus claves de proveedor de modelo, configuración de voz y atajos preferidos.

| SO | Artefacto de versión | Iniciar con |
| --- | --- | --- |
| Windows | `Wisp-<tag>-windows-x64.zip` | `Wisp.exe` |
| macOS | `Wisp-<tag>-macos-<arch>.zip` | `Wisp.app` |
| Linux | `Wisp-<tag>-linux-x64.tar.gz` | `Wisp` |

### Opción 2: Lanzador de repositorio

Usa esto si quieres ejecutar desde la fuente, desarrollar Wisp, o probar el último checkout.

Clona el repositorio:

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
```

Luego inicia Wisp con el lanzador de repositorio para tu plataforma:

| SO | Iniciar con | Fuente de dependencias |
| --- | --- | --- |
| Windows | `Start Wisp.bat` | `requirements.txt` |
| macOS | `Start Wisp.command` | `requirements-macos.lock` |
| Linux | `Start Wisp.sh` | `requirements.txt` |

El primer lanzamiento aprovisiona el entorno Python e instala las dependencias. Los lanzamientos posteriores van directamente a la aplicación.

Para construir tu propia copia empaquetada, consulta [Construir un EXE](docs/BUILDING_EXE.md) para los comandos de construcción local y el flujo de trabajo de versión etiquetada.

Requisitos:

- Python `3.12`, fijado en `.python-version`
- Windows 10/11, macOS 13+, o Linux con X11 para el camino completo de atajos/captura de pantalla
- Al menos una clave de proveedor LLM configurada o servidor compatible local

Para registros de ejecución completos, usa el lanzador de depuración correspondiente:

```text
Start Wisp Debug.bat
Start Wisp Debug.command
Start Wisp Debug.sh
```

## Configuración

Usa la ventana de Configuración para la configuración normal. Puede almacenar claves de proveedor, elegir rutas de modelo, configurar voz, ejecutar una verificación de configuración, explicar características opcionales faltantes, y mostrar advertencias para capacidades de modelo no compatibles.

Para builds de fuente y configuraciones avanzadas, `.env.example` documenta las claves de configuración disponibles. Por lo general, no necesitas editarlas manualmente.

## Fuentes de API de modelos gratuitas

Wisp es gratuito, y también puedes mantener los costos del modelo en cero. Varios proveedores ofrecen un nivel verdaderamente gratuito, créditos mensuales gratuitos o acceso limitado en velocidad sin costo. Wisp llega a la mayoría de ellos a través de su cliente compatible con OpenAI — algunos tienen un valor `LLM_PROVIDER` dedicado, y el resto funciona a través del endpoint `custom` apuntando `CUSTOM_BASE_URL` a la URL compatible con OpenAI del proveedor. Agrega la clave en **Configuración → LLM**.

| Proveedor | Qué es gratuito | Bueno para |
| --- | --- | --- |
| OpenRouter | Modelos `:free` — ~20 req/min y 50/día sin créditos, 1.000/día después de una recarga única de $10; más un router `openrouter/free` | La opción más fácil de "una API, muchos modelos" |
| Google AI Studio | Nivel gratuito de la API Gemini en regiones compatibles, con límites de velocidad | Trabajo multimodal y de contexto largo, incluyendo visión |
| Mistral | Nivel experimental gratuito en La Plateforme, con límite de velocidad | Modelos europeos amigables con RGPD y llamadas de funciones |
| NVIDIA | Acceso gratuito a la API de muchos modelos abiertos a través del Catálogo de API NVIDIA | Probar muchos modelos de peso abierto en endpoints alojados rápidos |
| GroqCloud | Nivel gratuito con límites de velocidad | Inferencia muy rápida para modelos abiertos como Llama y Qwen |
| Cerebras Inference | Nivel gratuito de API para modelos alojados en Cerebras | Inferencia de texto extremadamente rápida y creación de prototipos |
| GitHub Models | Acceso sin costo con límite de velocidad para cada cuenta de GitHub | Prototipado, experimentos, flujos de trabajo integrados con GitHub |
| Hugging Face Inference Providers | Créditos mensuales gratuitos (actualmente ~$0.10/mes para usuarios gratuitos) | Probar muchos modelos abiertos a través de un ecosistema |
| Cloudflare Workers AI | Plan gratuito de Workers con una asignación diaria gratuita | Aplicaciones ya en Cloudflare; endpoints de IA sin servidor |
| Vercel AI Gateway | Nivel gratuito con $5/mes de crédito de gateway para modelos elegibles | Proyectos Next.js/Vercel; acceso compatible con OpenAI unificado |
| SambaNova Cloud | $5 de crédito de API gratuito, sin tarjeta de crédito requerida | Inferencia rápida de modelos abiertos alojados |
| Puter.js | Acceso JS front-end a muchos modelos sin tu propia clave de API | Aplicaciones de navegador y demos; no es un proveedor backend de Wisp |
| Local — Ollama / LM Studio / vLLM | Gratuito cuando ejecutas el modelo tú mismo | Privacidad, sin facturación por token, endpoints locales compatibles con OpenAI |

Los niveles gratuitos tienen límites de velocidad y cambian con frecuencia, así que agrega al menos una ruta de respaldo y evita enviar contexto sensible a proveedores que puedan entrenar con tus prompts (la redacción de Wisp sigue aplicando). Para la guía completa de cómo conectar y advertencias, consulta la página **Fuentes de API gratuitas** en el [sitio de documentación de Wisp](Wisp%20Website/Wisp%20Docs.html).

## Atajos predeterminados

| Atajo | Acción |
| --- | --- |
| `Ctrl+Q` | Abrir el selector de intención general |
| `Ctrl+Shift+Q` | Abrir el selector de intención de reescritura/pegado |
| `Ctrl+Alt+Q` | Dibujar una captura de pantalla para visión |
| `Alt+Q` | Agregar la selección actual al búfer de contexto |
| `Alt+W` | Limpiar el búfer de contexto |
| Mantener `F9` | Grabar voz, transcribir y consultar |
| Mantener `F8` | Dictado directo en el campo de texto enfocado |
| `W` / `A` / `D` | Activar filas de intención integradas |
| `S` | Modo de prompt personalizado |
| `Esc` | Cancelar el selector |

Cada llamador, atajo, etiqueta, prompt, fuente de contexto, configuración de pegar de vuelta y dimensión de UI es configurable desde Configuración.

## Complementos

Los complementos son la forma compatible de extender Wisp. Cada complemento vive en su propia carpeta bajo `addons/` con un manifiesto `addon.toml`, y se ejecuta en su propio proceso de host Python aislado, por lo que un fallo, un hook lento o una dependencia incorrecta en un complemento no pueden derribar el worker cerebro ni ningún otro complemento. Las capacidades son opcionales: un complemento solo obtiene lo que su manifiesto declara, y los permisos faltantes son denegados. Los complementos que necesitan paquetes de terceros obtienen un entorno virtual dedicado que apruebas antes de que se ejecute.

Un complemento puede engancharse a Wisp en varios puntos:

- **Contexto** — leer o reescribir el prompt y el contexto antes de que se envíe una consulta.
- **Herramientas** — registrar herramientas llamables por el modelo que el modelo puede invocar durante la respuesta.
- **Respuestas** — observar las respuestas completadas para registrarlas, guardarlas o reenviarlas.
- **Intenciones y atajos** — agregar sus propias filas de intención y atajos globales con prompts personalizados.
- **UI** — contribuir acciones de bandeja, campos de configuración y notificaciones.
- **Acciones LLM** — ejecutar sus propias llamadas de modelo limitadas desde un hook o atajo.

**Qué pueden hacer los complementos:** porque un complemento puede inyectar contexto, exponer herramientas y reaccionar a las respuestas, la superficie es amplia. Algunos ejemplos, y el hook que cada uno usa:

| Quieres... | Hook | El manifiesto necesita |
| --- | --- | --- |
| Extraer tu git diff, calendario, o un ticket abierto al prompt automáticamente | Contexto (`before_query`) | `query = "modify"` |
| Dar al modelo una herramienta para buscar en un wiki interno, consultar una base de datos, llamar a una API de clima o bolsa, o alternar un dispositivo de hogar inteligente | Herramientas (`get_tools`) | `tools = true` (más `[dependencies]` para cualquier paquete) |
| Redactar o etiquetar el contexto sensible saliente para cumplimiento | Contexto (`before_query`) | `query = "modify"` |
| Agregar cada respuesta a un diario diario, o empujarla a Notion o Slack | Respuestas (`after_response`) | `response = "read"` |
| Agregar una intención de "reescribir en nuestro estilo de casa" de una tecla respaldada por su propio prompt | Intenciones y atajos | `[[intents]]` / `[[hotkeys]]`, `hotkeys = true` |

Si puedes escribirlo en Python y encaja en uno de los puntos de hook anteriores, puedes conectarlo a la misma superposición controlada por atajos que ya usas.

El complemento incluido `addons/healthcheck` es una referencia funcional que ejercita cada hook. Consulta la [Guía de complementos](addons/README.md) para el contrato completo de manifiesto y hook, o la página **Complementos** en el [sitio de documentación de Wisp](Wisp%20Website/Wisp%20Docs.html).

## Privacidad y control

Wisp está diseñado como un asistente de escritorio local. El almacenamiento permanece en tu máquina, y las solicitudes van directamente al proveedor de modelo o servidor local que configuras.

- Los datos locales se mantienen locales: la configuración, los chats, la memoria, los informes de privacidad y la configuración se almacenan en tu máquina.
- Las solicitudes del modelo van directamente desde tu máquina al proveedor o servidor local que configuraste.
- Tu proveedor de modelo configurado solo recibe el prompt que envías y las fuentes de contexto seleccionadas o habilitadas para ese llamador.
- Wisp puede inspeccionar el contexto disponible localmente para mostrar estimaciones de tokens, disponibilidad y recuentos de redacción de privacidad antes de que envíes. Previsualizar una fuente no la envía al proveedor de modelo ni la guarda como chat/memoria.
- El contexto se controla por perfil de atajo: el contexto de aplicación ambiental, el portapapeles, los documentos, las páginas del navegador, el contexto de GitHub, la memoria, las herramientas y las capturas de pantalla pueden habilitarse, deshabilitarse o enrutarse según demanda.
- El modo privacidad mantiene habilitadas las verificaciones de configuración y el comportamiento de advertencia orientados a la privacidad, incluyendo el estado de redacción antes de que se envíe el contexto sensible.
- La voz opcional, la lectura de documentos, el contenido del navegador, las capturas de pantalla, GitHub Copilot y los complementos permanecen inactivos hasta que se configuren.
- El TTS en la nube, los proveedores de modelo, los servidores compatibles o GitHub Copilot solo se contactan cuando configuras y usas esas características.
- Los complementos se ejecutan en procesos de host Python aislados y deben declarar las capacidades que necesitan.
- Las verificaciones de configuración evitan importar pilas pesadas de proveedor, audio o STT a menos que la característica esté habilitada.

## Estado de plataformas

| Plataforma | Estado |
| --- | --- |
| Windows 11 | Soporte completo |
| Windows 10 | Compatible |
| macOS 13+ | Compatible con trabajo nativo/audio aislado en workers |
| Linux X11 | Funcional |
| Linux Wayland | Limitado; usa X11 para el camino completo de atajos/captura de pantalla |

## Comentarios y ayuda de plataformas

Los informes de bugs son bienvenidos, especialmente para comportamientos de escritorio que dependen de permisos del SO, gestores de ventanas, dispositivos de audio o servidores de pantalla. Si encuentras un fallo, permiso faltante, atajo roto, problema de captura, fallo de pegado, o advertencia de verificación de configuración que parece incorrecta, por favor abre un issue con tu versión del SO, lanzador, registros y la acción que lo desencadenó.

La ayuda para probar y mejorar el soporte de macOS y el soporte de Linux Wayland es especialmente útil. Estas plataformas tienen la mayoría de los casos límite de integración nativa, por lo que los informes del mundo real de diferentes máquinas, entornos de escritorio y estados de permisos hacen que Wisp sea mejor para todos.

<details>
<summary>Documentación para contribuidores</summary>

- [README del desarrollador](docs/DEVELOPER_README.md) — configuración, puntos de entrada de ejecución, verificaciones y notas de depuración.
- [Visión general del código](docs/OVERVIEW.md) — propiedad de subsistemas y límites de ejecución.
- [Guía de complementos](addons/README.md) — manifiesto de complemento, permisos, hooks, herramientas, atajos y empaquetado.
- [Construir un EXE](docs/BUILDING_EXE.md) — notas de empaquetado de Windows.

</details>

## Licencia

MIT
