# MusicMatcher Local

[English](README.md) · [Arquitectura](docs/architecture.md) · [Colaborar](CONTRIBUTING.md)

Importa una lista de canciones, encuentra grabaciones equivalentes en YouTube, revisa las coincidencias dudosas y procesa el audio cuya descarga esté autorizada. Se ejecuta en tu ordenador mediante una web local o una CLI. La búsqueda utiliza yt-dlp y reglas deterministas en Python. **No utiliza modelos de IA, cuentas de modelos, claves de API para modelos ni llamadas de inferencia.**

Los metadatos no permiten demostrar que dos vídeos contengan exactamente la misma grabación o máster. Ante la duda, la aplicación deja la canción para revisión.

## Instalación

Necesitas **Python 3.11 o superior**. Instala [FFmpeg y ffprobe](https://ffmpeg.org/download.html) para procesar audio y [Deno 2.3 o superior](https://docs.deno.com/runtime/getting_started/installation/) para el soporte completo de YouTube. Sus ejecutables deben estar en `PATH`. El paquete Python llamado `ffmpeg` no instala el programa FFmpeg.

Descarga o clona este repositorio y abre una terminal en su carpeta raíz.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\musicmatcher.exe web
```

Adapta la versión de Python si tienes instalada otra compatible. No necesitas activar el entorno ni cambiar la política de ejecución de PowerShell.

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/musicmatcher web
```

En Debian/Ubuntu, `sudo apt install python3-venv ffmpeg` instala el soporte de entornos virtuales y las herramientas de audio. Instala Deno por separado siguiendo su documentación. En macOS, utiliza versiones de FFmpeg y Deno compatibles con tu equipo.

Tras la instalación inicial, `start_windows.cmd` o `sh start.sh` abren la aplicación. Mantén abierta la terminal mientras la usas. Son lanzadores del código fuente, no ejecutables autónomos.

La interfaz se abre en **http://127.0.0.1:8765**. Puedes elegir otro puerto o abrir el navegador tú mismo:

```bash
musicmatcher web --port 8766 --no-browser
```

En los ejemplos siguientes, `musicmatcher` representa el ejecutable del entorno virtual. También puedes usar `python -m musicmatcher` con el Python de ese entorno.

## Primer uso

1. Importa un CSV/JSON desde la web o introduce una URL de playlist de Spotify accesible.
2. En Configuración, selecciona carpeta de salida, bitrate MP3 y máximo de candidatos. El máximo es **8 por defecto**; puedes elegir entre **1 y 25**. Se consulta primero **1** y solo se amplía si hay dudas.
3. Ejecuta la búsqueda y sigue la fase actual, el progreso, el tiempo restante estimado y los errores individuales.
4. Comprueba las grabaciones dudosas y acepta sus enlaces cuando correspondan a la versión buscada.
5. Registra el permiso de descarga y su procedencia antes de procesar el audio.

Spotify es opcional. Los CSV/JSON funcionan sin cuenta ni aplicación de desarrollador. Buscar no descarga audio. Aceptar una coincidencia y declarar su permiso de descarga son decisiones independientes. La aplicación guarda tu declaración, pero no comprueba licencias automáticamente. Que un vídeo sea público u oficial no demuestra que se permita descargarlo.

## Formato de entrada

Utiliza UTF-8, con o sin BOM. Son obligatorios `title` y `artists` o `artist`. Son opcionales `album`, `duration_ms`, `duration_seconds`, `duration`, `isrc` y `explicit`.

```csv
title,artists,album,duration_seconds
Canción de ejemplo,Artista de ejemplo,Álbum de ejemplo,210
Otra canción,Artista de ejemplo|Invitado,Otro álbum,03:45
```

Separa colaboradores con `|` o `;`. No se dividen automáticamente las comas, porque pueden formar parte de un nombre artístico. Encierra entre comillas los valores que incluyan el delimitador del CSV. Se admiten delimitadores de coma, punto y coma o tabulación y cabeceras españolas como `Título`, `Artistas`, `Álbum` y `Duración`.

El JSON puede ser una lista o un objeto con `tracks`:

```json
{
  "tracks": [
    {
      "title": "Canción de ejemplo",
      "artists": ["Artista de ejemplo", "Invitado"],
      "album": "Álbum de ejemplo",
      "duration_ms": 210000
    }
  ]
}
```

`duration` numérico representa segundos; también se aceptan `mm:ss` y `hh:mm:ss`. Puedes omitir la duración, pero faltará esa comprobación para la selección automática. La web y la CLI admiten archivos de hasta 5 MB y 10.000 canciones. Se conservan los títulos originales, el orden y las repeticiones. Los errores de formato se detectan antes de reemplazar el catálogo guardado.

Importar otro catálogo requiere reemplazo explícito. Los archivos importados no conceden permisos: sus autorizaciones de descarga y registros de finalización no se consideran fiables. Al reimportar el mismo catálogo se pueden reutilizar decisiones compatibles que ya existan localmente.

## Configuración opcional de Spotify

Crea una aplicación propia en el [panel de desarrolladores de Spotify](https://developer.spotify.com/dashboard). Introduce su **Client ID** en la configuración local; no hace falta Client Secret. Registra exactamente esta Redirect URI:

```text
http://127.0.0.1:8888/callback
```

La primera importación abre el navegador para autorizar Spotify mediante PKCE y un callback temporal local. El token renovable se guarda en la carpeta de datos. Puedes cancelar la autorización; la espera máxima es de 180 segundos. El puerto del callback debe estar libre. En esta configuración Spotify no acepta sustituir la IP explícita por `localhost`.

En Development Mode, el propietario necesita Premium y las cuentas adicionales deben estar autorizadas, dentro del límite documentado. El endpoint actual `/items` permite leer listas propias o colaborativas; una URL pública ajena no basta. Las cuotas pueden detener la extracción. Si falla o la playlist cambia durante la lectura de páginas, se conserva el catálogo anterior. Consulta [cuotas y usuarios](https://developer.spotify.com/documentation/web-api/concepts/quota-modes), [acceso a playlists](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items) y la [actualización de la migración de 2026](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security).

El repositorio no aporta una aplicación compartida ni credenciales de Spotify. No descarga audio de Spotify ni incrusta automáticamente sus carátulas. Las imágenes deben tener autorización independiente. Los metadatos conservan su procedencia y enlaces. Al desconectar Spotify, elimina los tokens y datos de cuenta retenidos mediante los controles de desconexión/vaciado o los archivos locales indicados. [Condiciones para desarrolladores](https://developer.spotify.com/terms).

## CLI

### Reglas flexibles y pruebas por lotes

La aplicación utiliza ahora el modo **Flexible** por defecto. Admite títulos con créditos de colaboradores, nombres de álbum y etiquetas promocionales, sin exigir metadatos musicales estructurados de YouTube. La duración admite `max(12 segundos, 5 %)`. No conocer el indicador explícito/censurado genera una advertencia; una contradicción conocida sigue descartándose. También se mantienen los descartes de versiones incompatibles, intérpretes contradictorios, enlaces no accesibles y números de obra distintos.

La selección flexible exige al menos 78 puntos y 2 de ventaja frente a alternativas, salvo publicaciones con metadatos equivalentes. Las puntuaciones no son probabilidades. El modo **Estricto** anterior sigue disponible en «Reglas de coincidencia».

La búsqueda es **adaptativa**: primero solicita un resultado de YouTube y sus metadatos completos. Puede terminar ahí si el título y artista coinciden claramente, la duración difiere como máximo 3 segundos, hay indicios de publicación oficial y no hay advertencias de versiones o colaboradores dudosos. Esta salida rápida exige al menos 90 puntos, además de las reglas del modo elegido. Si falta evidencia, hay contradicciones o el primer vídeo no está disponible, solicita hasta el máximo configurado y compara las alternativas. Reutiliza los metadatos del primero. Con `--candidates 3` consulta **1 o hasta 3 vídeos en total**, nunca 1 + 3. La web muestra el número realmente evaluado. Si siguen existiendo dudas, mantiene la revisión manual. Una decisión que termina tras un único vídeo no permite comparar alternativas que no se han consultado.

El campo «Procesar primeras canciones» permite probar un lote sin eliminar el resto del catálogo. Vacío significa toda la lista. En CLI:

```bash
musicmatcher search --candidates 3 --matching-mode relaxed --first 10 --refresh
musicmatcher download --first 10
```

La segunda fase sigue exigiendo autorización de descarga vinculada a cada URL. Reducir las reglas musicales no concede esos permisos. La validación de duración del MP3 utiliza el margen del modo seleccionado, además de comprobar el audio y detectar truncamientos.

```bash
musicmatcher import canciones.csv
musicmatcher spotify "https://open.spotify.com/playlist/ID_DE_TU_PLAYLIST"
musicmatcher search --candidates 8
musicmatcher review
musicmatcher accept "Clave exacta mostrada en review" "https://www.youtube.com/watch?v=VIDEO_ID"
musicmatcher authorize "Clave exacta mostrada en review" --note "Permiso y su procedencia"
musicmatcher download
musicmatcher export
musicmatcher doctor
```

Elige importación de archivo o Spotify para el catálogo activo. `VIDEO_ID` es un marcador: sustitúyelo por un enlace real que hayas comprobado. Utiliza las claves exactas de la revisión, incluidos los sufijos de títulos repetidos.

```bash
musicmatcher import otra-lista.json --replace
musicmatcher search --candidates 12 --refresh
musicmatcher run canciones.csv --candidates 8
musicmatcher --data-dir ./otro-espacio web
```

`run` encadena las fases y reutiliza el trabajo guardado; deja pendientes las canciones sin revisión o permiso. Actualizar búsquedas conserva las decisiones manuales. Consulta `musicmatcher COMANDO --help` para ver sus opciones completas. `doctor` comprueba dependencias sin autorizar cuentas.

## Datos, reanudación y audio

Por defecto, los datos se guardan en `~/.musicmatcher` (`C:\Users\TU_USUARIO\.musicmatcher` en Windows). Puedes elegir otra carpeta con `MUSICMATCHER_DATA_DIR` o `--data-dir` antes del comando. También se admite un `.env` opcional basado en `.env.example`; mantén privado el archivo rellenado.

| Datos | Contenido |
|---|---|
| `estado.json` | Metadatos originales, posiciones, candidatos, decisiones, permisos y descargas. |
| `mapping.json` | Exportación ordenada con metadatos, estados, motivos y enlaces elegidos. |
| `canciones.json` | Diccionario de claves únicas de canción a URLs aceptadas. |
| `revision.json` | Pendientes y motivos de cada decisión. |
| `settings.json` | Configuración local de búsqueda, salida y Spotify. |
| `.spotify-cache-*` | Tokens renovables; no los compartas. |
| `job.json` / `events.log` | Progreso del proceso y eventos; revísalos antes de compartirlos. |

Conserva el estado completo además del diccionario de enlaces: las URLs no contienen álbum, artistas separados, duración o permisos. Usa carpetas de datos distintas para catálogos independientes y evita abrir dos instancias sobre la misma carpeta.

La descarga selecciona el mejor audio disponible y FFmpeg lo convierte. El MP3 usa 320 kbps por defecto; aumentar el bitrate no recupera información perdida en la fuente. Los nombres incluyen título y artistas originales y un identificador. Solo se sanea el nombre físico para hacerlo portable: los ID3 conservan los metadatos originales. Las repeticiones exactas reutilizan el archivo. La lista M3U8 generada conserva el orden y las repeticiones de los archivos completados.

Se guarda el progreso de cada canción y las fases reutilizan los resultados compatibles. Una interrupción conserva los temporales recuperables. Los MP3 finales se comprueban antes de reutilizarse; si están dañados, se conservan y se informa del error. Cambiar la URL, metadatos, calidad o portada puede generar otra salida.

YouTube puede cambiar, bloquear solicitudes o eliminar vídeos. Se registran los errores individuales y los bloqueos generales pausan la búsqueda. No se implementan cookies de cuentas, evasión de DRM, rotación de proxies ni evasión de controles de acceso. Procesa únicamente contenido cuya descarga permitan tanto el titular como el servicio. [Condiciones de YouTube](https://www.youtube.com/static?template=terms), [notas de yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube).

## Desarrollo y distribución

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
python -m build
```

Las pruebas simulan la red y generan audio local; no necesitan credenciales ni descargan canciones. Las comprobaciones dependientes de FFmpeg pueden omitirse cuando falten los ejecutables. CI incluye Windows, Linux y macOS con Python 3.11/3.12; configurar esa matriz no significa que ya se haya verificado cada plataforma o servicio real.

El proyecto queda preparado para publicarse, pero no crea ni sube por sí mismo un repositorio remoto. El workflow de versiones genera un wheel y una distribución fuente a partir de una etiqueta de versión. **No genera un ejecutable autónomo ni incluye FFmpeg o Deno.** Los instaladores nativos quedan para una fase posterior.

El código propio tiene [licencia MIT](LICENSE). Las dependencias mantienen sus licencias; consulta [los avisos de terceros](THIRD_PARTY_NOTICES.md). yt-dlp y EJS utilizan una pareja de versiones fijada; actualízala de forma coordinada y vuelve a comprobar las pruebas cuando cambie YouTube.

Para colaborar o comunicar incidencias, consulta [CONTRIBUTING.md](CONTRIBUTING.md) y [SECURITY.md](SECURITY.md).
