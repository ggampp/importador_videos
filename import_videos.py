"""
import_videos.py — Importador automático de vídeos do YouTube para o LingQ.

Fluxo:
1. Lê config.json com a lista de canais e idiomas.
2. Usa yt-dlp para obter os N vídeos mais recentes de cada canal.
3. Mantem history.json apenas como historico local da execucao.
4. Abre o Edge dedicado em headless por alguns segundos só para extrair
   os cookies de sessao do lingq.com do perfil isolado.
5. Para cada vídeo novo:
     a. Baixa as legendas no idioma do canal via yt-dlp (.vtt).
     b. POSTa direto à API do LingQ (/api/v3/<lang>/lessons/import/)
        com o arquivo de legendas, replicando o payload da extensao.
6. Atualiza history.json e grava log em logs/.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Caminhos do projeto
# ----------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
HISTORY_PATH = PROJECT_DIR / "history.json"
LOGS_DIR = PROJECT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
YOUTUBE_ID_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{6,15})")


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    log_file = LOGS_DIR / f"import_{datetime.now().strftime('%Y-%m-%d')}.log"
    logger = logging.getLogger("lingq_import")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


log = setup_logging()


def decode_process_output(data: bytes | None) -> str:
    if not data:
        return ""

    encodings = ["utf-8", locale.getpreferredencoding(False), "cp1252"]
    for encoding in dict.fromkeys(encodings):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def yt_dlp_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def resolve_project_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def yt_dlp_cookie_args(config: dict[str, Any] | None) -> list[str]:
    if not config:
        return []
    cookies_file = resolve_project_path(str(config.get("youtube_cookies_file") or "").strip())
    if cookies_file and cookies_file.exists():
        return ["--cookies", str(cookies_file)]
    return []


def subtitle_language_selector(lang_code: str) -> str:
    languages = [f"{lang_code}.*", lang_code]
    if lang_code == "en":
        languages.append("en-orig")
    return ",".join(dict.fromkeys(languages))


def normalize_lookup_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def iter_json_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_strings(item)


def collect_collection_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"id", "pk", "collectionId", "collection_id"} and isinstance(item, (int, str)):
                ids.add(str(item))
            else:
                ids.update(collect_collection_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.update(collect_collection_ids(item))
    return ids


def extract_imported_lesson_index(payload: Any) -> tuple[set[str], set[str]]:
    video_ids: set[str] = set()
    titles: set[str] = set()

    for text in iter_json_strings(payload):
        video_ids.update(YOUTUBE_ID_RE.findall(text))
        if 5 <= len(text) <= 180:
            titles.add(normalize_lookup_text(text))

    return video_ids, titles


# ----------------------------------------------------------------------------
# Config e histórico
# ----------------------------------------------------------------------------
def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        log.error("Arquivo config.json nao encontrado em %s", CONFIG_PATH)
        sys.exit(1)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_history() -> dict[str, dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return {}
    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history: dict[str, dict[str, Any]]) -> None:
    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def get_configured_lingq_cookies(config: dict[str, Any]) -> dict[str, str]:
    cookies = config.get("lingq_cookies") or {}
    sessionid = str(cookies.get("sessionid") or "").strip()
    lingq_session = str(cookies.get("wwwlingqcomsa") or "").strip()
    csrftoken = str(cookies.get("csrftoken") or "").strip()
    if (sessionid or lingq_session) and csrftoken:
        configured = {"csrftoken": csrftoken}
        if sessionid:
            configured["sessionid"] = sessionid
        if lingq_session:
            configured["wwwlingqcomsa"] = lingq_session
        return configured
    return {}


# ----------------------------------------------------------------------------
# YouTube — buscar últimos vídeos via yt-dlp
# ----------------------------------------------------------------------------
def youtube_locale_args(lang_code: str | None) -> list[str]:
    """
    Argumentos para forçar o yt-dlp/YouTube a responder no idioma alvo,
    evitando que o YouTube devolva o título traduzido para inglês quando o
    dono do canal cadastra múltiplos títulos.
    """
    lc = (lang_code or "").strip().lower()
    if not lc:
        return []
    return [
        "--extractor-args", f"youtube:lang={lc}",
        "--extractor-args", f"youtubetab:lang={lc}",
        "--add-header", f"Accept-Language:{lc}",
    ]


def fetch_latest_videos(channel_url: str, limit: int, config: dict[str, Any] | None = None,
                        lang_code: str | None = None) -> list[dict[str, str]]:
    """
    Retorna [{ 'id': 'xxxx', 'url': 'https://www.youtube.com/watch?v=xxxx', 'title': '...' }, ...]
    usando yt-dlp em modo flat-playlist (rápido, sem baixar nada).

    `lang_code` (ex: "it", "es") força o YouTube a devolver o título no idioma
    original do canal em vez de uma tradução automática para inglês.
    """
    videos_url = channel_url.rstrip("/") + "/videos"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "--playlist-end", str(limit),
        "--print", "%(id)s|%(duration)s|%(title)s",
        "--no-warnings",
        "--quiet",
        *youtube_locale_args(lang_code),
        *yt_dlp_cookie_args(config),
        videos_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=60,
            env=yt_dlp_env(),
        )
    except subprocess.TimeoutExpired:
        log.error("Timeout ao buscar videos de %s", channel_url)
        return []
    except FileNotFoundError:
        log.error("yt-dlp nao encontrado. Instale com: pip install yt-dlp")
        sys.exit(1)

    if result.returncode != 0:
        log.error("yt-dlp falhou para %s: %s", channel_url, decode_process_output(result.stderr).strip())
        return []

    videos = []
    for line in decode_process_output(result.stdout).strip().splitlines():
        if not line or "|" not in line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        vid_id, duration_raw, title = parts
        vid_id = vid_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,15}", vid_id):
            continue
        duration: int | None
        try:
            duration = int(float(duration_raw.strip())) if duration_raw.strip() not in ("", "NA") else None
        except (ValueError, TypeError):
            duration = None
        videos.append({
            "id": vid_id,
            "url": f"https://www.youtube.com/watch?v={vid_id}",
            "title": title.strip(),
            "thumbnail": youtube_thumbnail_url(vid_id),
            "duration": duration,
        })

    # O extractor `youtubetab` (pagina de canal) ignora o arg `lang`, entao a
    # flat-playlist retorna titulos em ingles quando o dono cadastra titulos
    # multi-idioma. Refazemos uma consulta por video — desta vez no extractor
    # `youtube` (que respeita lang) — para obter o titulo no idioma original.
    if lang_code and videos:
        localized = fetch_localized_titles(
            [v["url"] for v in videos], lang_code, config
        )
        for v in videos:
            new_title = localized.get(v["id"])
            if new_title and new_title != v["title"]:
                log.info("    Titulo localizado [%s]: %s -> %s",
                         lang_code, v["title"][:50], new_title[:50])
                v["title"] = new_title
    return videos


def fetch_localized_titles(video_urls: list[str], lang_code: str,
                           config: dict[str, Any] | None = None) -> dict[str, str]:
    """
    Consulta yt-dlp por video (extractor `youtube`, que respeita `lang`) e
    devolve { video_id: titulo_no_idioma }. Falhas individuais sao ignoradas.
    """
    if not video_urls:
        return {}
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--print", "%(id)s|%(title)s",
        "--skip-download",
        "--no-warnings",
        "--quiet",
        "--ignore-errors",
        *youtube_locale_args(lang_code),
        *yt_dlp_cookie_args(config),
        *video_urls,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=max(30, 15 * len(video_urls)),
            env=yt_dlp_env(),
        )
    except subprocess.TimeoutExpired:
        log.warning("    Timeout ao buscar titulos localizados (%s)", lang_code)
        return {}

    titles: dict[str, str] = {}
    for line in decode_process_output(result.stdout).splitlines():
        if "|" not in line:
            continue
        vid_id, _, title = line.partition("|")
        vid_id = vid_id.strip()
        title = title.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{6,15}", vid_id) and title:
            titles[vid_id] = title
    return titles


def format_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def filter_videos_by_duration(videos: list[dict[str, Any]], canal: dict[str, Any],
                              config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Filtra `videos` mantendo apenas os que estao dentro de [min, max] segundos.
    Limites efetivos: override do canal > default do config.
    Videos sem duracao conhecida sao mantidos (a duracao real sera vista no momento da legenda).
    """
    def _to_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    min_s = _to_int(canal.get("duracao_min_segundos"))
    if min_s is None:
        min_s = _to_int(config.get("duracao_min_segundos"))
    max_s = _to_int(canal.get("duracao_max_segundos"))
    if max_s is None:
        max_s = _to_int(config.get("duracao_max_segundos"))

    if min_s is None and max_s is None:
        return videos

    kept = []
    for v in videos:
        d = v.get("duration")
        if d is None:
            kept.append(v)
            continue
        if min_s is not None and d < min_s:
            log.info("  Ignorado por duracao curta (%s < %s): %s",
                     format_seconds(d), format_seconds(min_s), v["title"][:60])
            continue
        if max_s is not None and d > max_s:
            log.info("  Ignorado por duracao longa (%s > %s): %s",
                     format_seconds(d), format_seconds(max_s), v["title"][:60])
            continue
        kept.append(v)
    return kept


def youtube_thumbnail_url(video_id: str) -> str:
    """
    Retorna uma thumbnail publica e estavel do YouTube para o LingQ buscar.
    A extensao LingQ Importer envia esse valor como external_image para videos.
    """
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


# ----------------------------------------------------------------------------
# Legendas - baixar via yt-dlp
# ----------------------------------------------------------------------------
def fetch_subtitles(video_id: str, video_url: str, lang_code: str, out_dir: Path,
                    config: dict[str, Any] | None = None) -> Path | None:
    """
    Baixa as legendas de um video do YouTube no idioma especificado via yt-dlp.
    Tenta primeiro legendas humanas/oficiais; se nao houver, tenta auto-geradas.
    Retorna o Path do arquivo .vtt baixado, ou None se nao houver legenda disponivel.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    for old in out_dir.glob(f"{video_id}*"):
        try:
            old.unlink()
        except Exception:
            pass

    output_template = str(out_dir / f"{video_id}.%(ext)s")

    def try_download(auto: bool) -> Path | None:
        flag = "--write-auto-subs" if auto else "--write-subs"
        cmd = [
            sys.executable, "-m", "yt_dlp",
            flag,
            "--sub-langs", subtitle_language_selector(lang_code),
            "--sub-format", "vtt/best",
            "--skip-download",
            "--no-warnings",
            "--quiet",
            "-o", output_template,
            *yt_dlp_cookie_args(config),
            video_url,
        ]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=False,
                timeout=120,
                env=yt_dlp_env(),
            )
        except subprocess.TimeoutExpired:
            log.error("    Timeout ao baixar legendas (%s, auto=%s)", lang_code, auto)
            return None
        if r.returncode != 0:
            stderr = decode_process_output(r.stderr).strip()
            if stderr:
                log.info("    yt-dlp nao baixou legendas (auto=%s): %s", auto, stderr[-500:])
            return None
        for f in out_dir.glob(f"{video_id}.*.vtt"):
            return f
        return None

    f = try_download(auto=False)
    if f:
        log.info("    Legendas humanas encontradas: %s", f.name)
        dedupe_vtt_rolling_captions(f)
        return f
    f = try_download(auto=True)
    if f:
        log.info("    Legendas auto-geradas encontradas: %s", f.name)
        dedupe_vtt_rolling_captions(f)
        return f
    return None


_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _strip_prefix_overlap(prev: str, curr: str) -> str:
    """Se `curr` começa com um sufixo de `prev`, retorna `curr` sem essa parte."""
    if not prev or not curr:
        return curr
    max_overlap = min(len(prev), len(curr))
    for n in range(max_overlap, 0, -1):
        if prev.endswith(curr[:n]):
            return curr[n:].lstrip()
    return curr


def dedupe_vtt_rolling_captions(vtt_path: Path) -> None:
    """
    Remove o efeito de "rolling captions" das legendas auto-geradas do YouTube
    (e overlaps similares produzidos por Whisper). Cada cue é comparado com o
    anterior e o sufixo repetido é descartado. Cues totalmente contidos são
    removidos. Reescreve o arquivo no lugar.
    """
    try:
        content = vtt_path.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("    Falha ao ler VTT para dedup: %s", exc)
        return

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    out_blocks: list[str] = []
    prev_text = ""
    kept_cues = 0
    dropped_cues = 0

    for block in blocks:
        timing_idx = next((i for i, ln in enumerate(block) if "-->" in ln), None)
        if timing_idx is None:
            out_blocks.append("\n".join(block))
            continue

        cue_id_lines = block[:timing_idx]
        timing = block[timing_idx]
        text_lines = block[timing_idx + 1:]

        raw_text = "\n".join(_VTT_TAG_RE.sub("", ln) for ln in text_lines)
        text = re.sub(r"\s+", " ", raw_text).strip()
        if not text:
            dropped_cues += 1
            continue

        new_text = _strip_prefix_overlap(prev_text, text)
        prev_text = text

        if not new_text:
            dropped_cues += 1
            continue

        cue_block = []
        cue_block.extend(cue_id_lines)
        cue_block.append(timing)
        cue_block.append(new_text)
        out_blocks.append("\n".join(cue_block))
        kept_cues += 1

    vtt_path.write_text("\n\n".join(out_blocks) + "\n", encoding="utf-8")
    if dropped_cues:
        log.info("    VTT dedup: %d cues mantidos, %d cues redundantes removidos",
                 kept_cues, dropped_cues)


def download_audio_for_transcription(video_id: str, video_url: str, out_dir: Path,
                                     config: dict[str, Any]) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{video_id}.audio.*"):
        try:
            old.unlink()
        except Exception:
            pass

    max_mb = int(config.get("transcricao_max_audio_mb", 24))
    output_template = str(out_dir / f"{video_id}.audio.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "--max-filesize", f"{max_mb}M",
        "--no-warnings",
        "--quiet",
        "-o", output_template,
        *yt_dlp_cookie_args(config),
        video_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=int(config.get("transcricao_download_timeout_segundos", 600)),
            env=yt_dlp_env(),
        )
    except subprocess.TimeoutExpired:
        log.error("    Timeout ao baixar audio para transcricao")
        return None

    if result.returncode != 0:
        stderr = decode_process_output(result.stderr).strip()
        log.warning("    Falha ao baixar audio para transcricao: %s", stderr[-500:] if stderr else "erro desconhecido")
        return None

    for audio_file in out_dir.glob(f"{video_id}.audio.*"):
        if audio_file.is_file():
            return audio_file
    return None


def transcribe_video_to_vtt(video: dict[str, str], lang_code: str, out_dir: Path,
                            config: dict[str, Any]) -> Path | None:
    api_key = os.environ.get("OPENAI_API_KEY") or str(config.get("openai_api_key") or "").strip()
    if not api_key:
        log.info("    Transcricao por IA indisponivel: configure OPENAI_API_KEY ou openai_api_key.")
        return None

    audio_file = download_audio_for_transcription(video["id"], video["url"], out_dir, config)
    if not audio_file:
        return None

    try:
        import requests
    except ImportError:
        log.warning("    Modulo 'requests' nao instalado; nao e possivel transcrever.")
        return None

    model = str(config.get("transcricao_modelo") or "whisper-1")
    log.info("    Transcrevendo audio com %s...", model)
    try:
        with audio_file.open("rb") as fh:
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "model": model,
                    "language": lang_code,
                    "response_format": "vtt",
                },
                files={"file": (audio_file.name, fh)},
                timeout=int(config.get("transcricao_timeout_segundos", 900)),
            )
    except Exception as exc:
        log.warning("    Falha na transcricao por IA: %s", exc)
        return None
    finally:
        try:
            audio_file.unlink()
        except Exception:
            pass

    if response.status_code != 200:
        log.warning("    Falha na transcricao por IA: HTTP %s: %s", response.status_code, response.text[:300])
        return None

    vtt_file = out_dir / f"{video['id']}.transcrito.{lang_code}.vtt"
    vtt_file.write_text(response.text, encoding="utf-8")
    log.info("    Transcricao por IA gerada: %s", vtt_file.name)
    dedupe_vtt_rolling_captions(vtt_file)
    return vtt_file


# ----------------------------------------------------------------------------
# Sessao LingQ - extrair cookies do perfil Edge dedicado
# ----------------------------------------------------------------------------
def get_lingq_session_cookies(profile_dir: Path, edge_exe: str | None) -> dict[str, str]:
    """
    Abre o Edge em modo headless brevemente no perfil dedicado e extrai
    os cookies de www.lingq.com (sessionid, csrftoken, etc).
    """
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "channel": "msedge",
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
            ],
            "ignore_default_args": ["--disable-extensions"],
        }
        if edge_exe and Path(edge_exe).exists():
            launch_kwargs["executable_path"] = edge_exe

        ctx = p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            cookies = ctx.cookies("https://www.lingq.com/")
        finally:
            ctx.close()

    return {c["name"]: c["value"] for c in cookies}


# ----------------------------------------------------------------------------
# API LingQ - POST direto da legenda a API de import
# ----------------------------------------------------------------------------
def import_to_lingq(video: dict[str, str], lang_code: str,
                    cookies: dict[str, str], subs_file: Path) -> tuple[bool, str]:
    """
    Faz POST multipart a API de import do LingQ com o arquivo de legendas.
    Replica o payload que a extensao LingQ Importer envia (popup.js).
    Retorna (sucesso, mensagem).
    """
    try:
        import requests
    except ImportError:
        return False, "modulo 'requests' nao instalado - rode: pip install -r requirements.txt"

    csrf = cookies.get("csrftoken", "")
    if not csrf:
        return False, "csrftoken nao encontrado nos cookies (sessao LingQ expirou?)"

    api_url = f"https://www.lingq.com/api/v3/{lang_code}/lessons/import/"

    title = video.get("title", "")
    if title.endswith(" - YouTube"):
        title = title[: -len(" - YouTube")]
    title = title[:120]

    try:
        with subs_file.open("rb") as fh:
            files = {"file": (subs_file.name, fh, "text/vtt")}
            data = {
                "url": video["url"],
                "title": title,
                "level": "0",
                "source": "Edge",
                "save": "true",
                "external_image": video.get("thumbnail") or youtube_thumbnail_url(video["id"]),
            }
            r = requests.post(
                api_url,
                files=files,
                data=data,
                cookies=cookies,
                headers={
                    "X-CSRFToken": csrf,
                    "Referer": "https://www.lingq.com/",
                    "User-Agent": "Mozilla/5.0 LingQ-Importer-Auto/1.0",
                },
                timeout=120,
            )
    except Exception as e:
        return False, f"excecao no POST: {e}"

    if r.status_code in (200, 201):
        try:
            body = r.json()
            lesson_url = body.get("lessonURL") or body.get("url") or ""
            return True, f"HTTP {r.status_code} | {lesson_url}".strip(" |")
        except Exception:
            return True, f"HTTP {r.status_code}"

    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def fetch_lingq_imported_index(lang_code: str, cookies: dict[str, str],
                               max_collections: int = 40) -> tuple[set[str], set[str]]:
    """
    Tenta montar um indice remoto das licoes do usuario no LingQ.
    A API de biblioteca do LingQ nao e bem documentada, entao este metodo e
    propositalmente tolerante: consulta alguns endpoints conhecidos e ignora
    falhas/formantos inesperados.
    """
    try:
        import requests
    except ImportError:
        log.warning("Modulo 'requests' nao instalado; pulando validacao remota no LingQ.")
        return set(), set()

    headers = {
        "Referer": "https://www.lingq.com/",
        "User-Agent": "Mozilla/5.0 LingQ-Importer-Auto/1.0",
    }
    endpoints = [
        f"https://www.lingq.com/api/v3/{lang_code}/lessons/?page_size=100",
        f"https://www.lingq.com/api/v3/{lang_code}/lessons/?limit=100",
        f"https://www.lingq.com/api/v2/{lang_code}/collections/my/",
    ]

    video_ids: set[str] = set()
    titles: set[str] = set()
    collection_ids: set[str] = set()

    def get_json(url: str) -> Any | None:
        try:
            response = requests.get(url, cookies=cookies, headers=headers, timeout=25)
        except Exception as exc:
            log.info("  Validacao LingQ indisponivel para %s: %s", url, exc)
            return None
        if response.status_code != 200:
            log.info("  Validacao LingQ ignorou %s: HTTP %s", url, response.status_code)
            return None
        try:
            return response.json()
        except Exception:
            return None

    for endpoint in endpoints:
        payload = get_json(endpoint)
        if payload is None:
            continue
        ids, found_titles = extract_imported_lesson_index(payload)
        video_ids.update(ids)
        titles.update(found_titles)
        if "/collections/my/" in endpoint:
            collection_ids.update(collect_collection_ids(payload))

    for collection_id in sorted(collection_ids)[:max_collections]:
        payload = get_json(f"https://www.lingq.com/api/v2/{lang_code}/collections/{collection_id}/")
        if payload is None:
            continue
        ids, found_titles = extract_imported_lesson_index(payload)
        video_ids.update(ids)
        titles.update(found_titles)

    return video_ids, titles


def remove_videos_already_in_lingq(plano: list[dict[str, str]], cookies: dict[str, str],
                                   history: dict[str, dict[str, Any]],
                                   max_collections: int) -> list[dict[str, str]]:
    remaining: list[dict[str, str]] = []
    indexes: dict[str, tuple[set[str], set[str]]] = {}

    for video in plano:
        lang_code = video["lang_code"]
        if lang_code not in indexes:
            log.info("Validando licoes ja existentes no LingQ (lang=%s)...", lang_code)
            indexes[lang_code] = fetch_lingq_imported_index(lang_code, cookies, max_collections)
            log.info("  Indice LingQ lang=%s: %d IDs YouTube | %d textos/titulos",
                     lang_code, len(indexes[lang_code][0]), len(indexes[lang_code][1]))

        remote_ids, remote_titles = indexes[lang_code]
        title_key = normalize_lookup_text(video["title"])
        if video["id"] in remote_ids or title_key in remote_titles:
            log.info("  Ja existe no LingQ, pulando: %s", video["title"][:80])
            history[video["id"]] = {
                "titulo": video["title"],
                "canal": video["canal"],
                "idioma": video["idioma"],
                "url": video["url"],
                "importado_em": datetime.now().isoformat(timespec="seconds"),
                "automatico": True,
                "resultado": "ja existia no LingQ (validacao remota)",
            }
            save_history(history)
            continue
        remaining.append(video)

    return remaining


# ----------------------------------------------------------------------------
# Orquestracao principal
# ----------------------------------------------------------------------------
def run_import(config: dict[str, Any], history: dict[str, dict[str, Any]]) -> None:
    profile_dir = Path(config["edge_profile_dir"])
    profile_dir.mkdir(parents=True, exist_ok=True)

    edge_exe = config.get("edge_executable") or None
    delay_videos = int(config.get("delay_entre_videos_segundos", 5))
    plano: list[dict[str, str]] = []
    for canal in config["canais"]:
        if canal.get("ativo") is False:
            log.info("Canal desativado: %s [%s]", canal["nome"], canal["idioma"])
            continue

        limit_per_channel = int(canal.get("videos_por_execucao", config.get("videos_por_canal", 1)))
        log.info("Canal: %s [%s]", canal["nome"], canal["idioma"])
        latest = fetch_latest_videos(canal["url"], limit_per_channel, config,
                                     lang_code=canal.get("lang_code"))
        if not latest:
            log.warning("  Sem videos retornados (canal vazio ou bloqueado).")
            continue
        latest = filter_videos_by_duration(latest, canal, config)
        if not latest:
            log.info("  Nenhum video dentro da faixa de duracao configurada.")
            continue
        for v in latest:
            v["canal"] = canal["nome"]
            v["idioma"] = canal["idioma"]
            v["lang_code"] = canal["lang_code"]
            plano.append(v)
            log.info("  Novo para importar: %s", v["title"][:60])

    if not plano:
        log.info("Nada novo para importar hoje. Encerrando.")
        return

    log.info("Total de videos novos a importar: %d", len(plano))

    cookies = get_configured_lingq_cookies(config)
    if cookies:
        log.info("Usando cookies LingQ configurados para importar via API.")
    else:
        log.info("Extraindo cookies de sessao LingQ do perfil Edge...")
        try:
            cookies = get_lingq_session_cookies(profile_dir, edge_exe)
        except Exception as e:
            log.error("Falha ao abrir Edge para extrair cookies: %r", e)
            log.error("Para evitar Edge, configure sessionid e csrftoken do LingQ na interface web.")
            log.error("Se preferir usar o perfil Edge, feche todas as janelas desse perfil e confira edge_profile_dir.")
            return

    if "sessionid" not in cookies and "csrftoken" not in cookies:
        log.error("Nenhum cookie de sessao do LingQ encontrado no perfil.")
        log.error("Abra https://www.lingq.com/login no perfil dedicado e faca login. Depois rode o script de novo.")
        return
    log.info("Cookies LingQ encontrados (%d): %s", len(cookies), ", ".join(sorted(cookies.keys())))

    if config.get("validar_importados_no_lingq", True):
        max_collections = int(config.get("validacao_lingq_max_colecoes", 40))
        plano = remove_videos_already_in_lingq(plano, cookies, history, max_collections)
        if not plano:
            log.info("Todos os videos novos locais ja existem no LingQ. Encerrando.")
            return

    subs_dir = PROJECT_DIR / "subs_tmp"
    subs_dir.mkdir(exist_ok=True)

    sucesso = 0
    falha_sem_legenda = 0
    falha_api = 0

    for idx, video in enumerate(plano, 1):
        log.info("[%d/%d] %s - %s", idx, len(plano), video["canal"], video["title"][:80])

        log.info("  Baixando legendas (lang=%s)...", video["lang_code"])
        subs_file = fetch_subtitles(video["id"], video["url"], video["lang_code"], subs_dir, config)
        if not subs_file and config.get("transcrever_sem_legenda", False):
            log.info("  Tentando transcricao por IA como fallback...")
            subs_file = transcribe_video_to_vtt(video, video["lang_code"], subs_dir, config)
        if not subs_file:
            log.warning("  Sem legendas disponiveis para %s. Pulando.", video["lang_code"])
            history[video["id"]] = {
                "titulo": video["title"],
                "canal": video["canal"],
                "idioma": video["idioma"],
                "url": video["url"],
                "importado_em": datetime.now().isoformat(timespec="seconds"),
                "automatico": False,
                "resultado": "sem legendas disponiveis",
            }
            save_history(history)
            falha_sem_legenda += 1
            if idx < len(plano):
                time.sleep(delay_videos)
            continue

        ok, msg = import_to_lingq(video, video["lang_code"], cookies, subs_file)
        if ok:
            log.info("  IMPORTADO: %s", msg)
            sucesso += 1
        else:
            log.error("  FALHA: %s", msg)
            falha_api += 1

        history[video["id"]] = {
            "titulo": video["title"],
            "canal": video["canal"],
            "idioma": video["idioma"],
            "url": video["url"],
            "importado_em": datetime.now().isoformat(timespec="seconds"),
            "automatico": ok,
            "resultado": msg,
        }
        save_history(history)

        try:
            subs_file.unlink()
        except Exception:
            pass

        if idx < len(plano):
            time.sleep(delay_videos)

    log.info("=" * 60)
    log.info("Resumo: %d importados | %d sem legenda | %d erro API | total %d",
             sucesso, falha_sem_legenda, falha_api, len(plano))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    log.info("=" * 60)
    log.info("Inicio: %s", datetime.now().isoformat(timespec="seconds"))
    config = load_config()
    history = load_history()
    active_channels = [canal for canal in config["canais"] if canal.get("ativo") is not False]
    log.info("Canais ativos/configurados: %d/%d | Registros no historico local: %d",
             len(active_channels), len(config["canais"]), len(history))
    try:
        run_import(config, history)
    except KeyboardInterrupt:
        log.warning("Interrompido pelo usuario.")
        return 130
    except Exception:
        log.exception("Erro inesperado:")
        return 1
    log.info("Fim: %s", datetime.now().isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
