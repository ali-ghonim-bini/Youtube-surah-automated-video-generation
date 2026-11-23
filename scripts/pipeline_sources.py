"""Downloads with retries, and the surah text/translation from surahquran.com."""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports
from pipeline_fonts import normalize_search_text


def fetch_page(number: int) -> str:
    url = TEXT_PAGE_URL_TEMPLATE.format(number=number)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read().decode("utf-8", errors="replace")
            log("FETCH_OK", surah=number, url=url, bytes=len(body), attempt=attempt)
            return body
        except Exception as exc:
            last_error = exc
            log("FETCH_RETRY", level="WARNING", surah=number, url=url, attempt=attempt, message=str(exc))
    raise RuntimeError(f"Failed to fetch surah page after retries: {last_error}") from last_error


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value))).strip()


def extract_surah_data(number: int, page: str) -> tuple[str, list[str], list[str]]:
    text = html.unescape(page)
    name_match = re.search(r"\|\s*سورة\s+[^|<]+\|\s*([^\-<|]+?)\s*-\s*عدد آياتها", text)
    if not name_match:
        name_match = re.search(r"معنى السورة بالإنجليزية:\s*([^.<]+)", text)
    english_name = strip_tags(name_match.group(1)) if name_match else f"Surah {number}"

    arabic_ayat: list[str] = []
    translations: list[str] = []
    pattern = re.compile(
        r"(?P<ayah>[\u0600-\u06FF\u0670\u06D6-\u06ED\sًٌٍَُِّْٰـ]+)\s*\((?P<num>\d+)\)\s*<[^>]*>\s*<a[^>]+english-aya-\d+-sora-"
        + str(number)
        + r"\.html[^>]*>(?P<translation>.*?)</a>",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        ayah_number = int(match.group("num"))
        ayah = strip_tags(match.group("ayah"))
        translation = strip_tags(match.group("translation"))
        if ayah and translation and ayah_number == len(arabic_ayat) + 1:
            arabic_ayat.append(f"{ayah} ({ayah_number})")
            translations.append(translation)

    if not arabic_ayat:
        raise RuntimeError(f"Could not extract ayat for surah {number}")
    log("EXTRACT_OK", surah=number, english_name=english_name, ayat=len(arabic_ayat), translations=len(translations))
    return english_name, arabic_ayat, translations


NETWORK_RETRY_WAITS = (5, 20, 60)   # seconds between attempts of one download
EXIT_NETWORK_DEFERRED = 3           # surah stays in TODO; the watcher tries again later



def is_network_error(exc: BaseException | None) -> bool:
    """Timeouts, dropped connections, DNS failures and server-side (5xx/429) errors: worth retrying later."""
    while exc is not None:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code >= 500 or exc.code == 429
        if isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def with_network_retries(what: str, action):
    for attempt in range(1, len(NETWORK_RETRY_WAITS) + 2):
        try:
            return action()
        except Exception as exc:
            if not is_network_error(exc) or attempt > len(NETWORK_RETRY_WAITS):
                raise
            wait = NETWORK_RETRY_WAITS[attempt - 1]
            log("NETWORK_RETRY", level="WARNING", what=what, attempt=attempt, wait_seconds=wait, error=str(exc))
            time.sleep(wait)


def fetch_json(url: str) -> dict[str, object]:
    def action() -> dict[str, object]:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    return with_network_retries(url, action)


def fetch_text(url: str) -> str:
    def action() -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    return with_network_retries(url, action)


def validate_audio_source_page() -> None:
    page = fetch_text(AUDIO_RECITATION_PAGE_URL)
    normalized_page = normalize_search_text(page)
    normalized_sheikh = normalize_search_text(EXPECTED_SHEIKH_NAME)
    missing_terms = [term for term in EXPECTED_AUDIO_SOURCE_TERMS if normalize_search_text(term) not in normalized_page]
    if normalized_sheikh not in normalized_page:
        log(
            "AUDIO_SOURCE_VALIDATE_ERROR",
            level="ERROR",
            expected_sheikh=EXPECTED_SHEIKH_NAME,
            normalized_expected_sheikh=normalized_sheikh,
            recitation_page_url=AUDIO_RECITATION_PAGE_URL,
            message="Expected Sheikh name was not found in audio source page after normalized matching",
        )
        raise RuntimeError(f"Audio source Sheikh validation failed for expected Sheikh: {EXPECTED_SHEIKH_NAME}")
    if missing_terms:
        log(
            "AUDIO_SOURCE_VALIDATE_ERROR",
            level="ERROR",
            expected_terms=EXPECTED_AUDIO_SOURCE_TERMS,
            missing_terms=missing_terms,
            recitation_page_url=AUDIO_RECITATION_PAGE_URL,
            message="Audio source page is missing expected recitation terms",
        )
        raise RuntimeError(f"Audio source validation failed, missing terms: {', '.join(missing_terms)}")
    log(
        "AUDIO_SOURCE_VALIDATE_OK",
        expected_sheikh=EXPECTED_SHEIKH_NAME,
        normalized_expected_sheikh=normalized_sheikh,
        expected_terms=EXPECTED_AUDIO_SOURCE_TERMS,
        recitation_id=AUDIO_RECITATION_ID,
        recitation_page_url=AUDIO_RECITATION_PAGE_URL,
    )


def download_file(url: str, out: Path) -> None:
    if out.exists() and out.stat().st_size > 0:
        log("DOWNLOAD_SKIP", file=out, reason="already exists")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    def action() -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as response, tmp.open("wb") as f:
            shutil.copyfileobj(response, f)
    with_network_retries(url, action)
    tmp.replace(out)
    log("DOWNLOAD_OK", file=out, bytes=out.stat().st_size, url=url)
