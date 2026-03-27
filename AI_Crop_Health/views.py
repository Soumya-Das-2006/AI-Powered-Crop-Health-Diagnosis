from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.core.cache import cache
from django.conf import settings
import hashlib
import json
import logging
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contact.models import Service, Testimonial
from blog.models import BlogPost
from features.models import GovernmentScheme

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "gu": "Gujarati",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "or": "Odia",
    "ur": "Urdu",
    "as": "Assamese",
}

def home(request):
    """Render the home page"""
    services = Service.objects.filter(is_active=True)
    testimonials = Testimonial.objects.filter(is_active=True)
    latest_posts = BlogPost.objects.filter(is_published=True).order_by('-publish_date')[:3]
    schemes = GovernmentScheme.objects.filter(is_active=True) \
        .order_by('-last_updated')[:3]

    context = {
        'services': services,
        'govt_schemes': schemes,
        'testimonials': testimonials,
        'latest_posts': latest_posts,
    }
    return render(request, 'ai_crop_health/index.html', context)

def about(request):
    """Render the about page"""
    return render(request, 'ai_crop_health/about.html')


def _make_cache_key(target_lang: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"translate:{target_lang}:{digest}"


def _libretranslate_endpoints() -> list:
    configured = getattr(settings, "LIBRETRANSLATE_URL", "").strip()
    fallback = [
        "https://libretranslate.de/translate",
        "https://translate.argosopentech.com/translate",
    ]
    if configured:
        return [configured] + [url for url in fallback if url != configured]
    return fallback


def _translate_batch(texts, target_lang, source_lang="en"):
    if not texts:
        return []

    payload = {
        "q": texts,
        "source": source_lang,
        "target": target_lang,
        "format": "text",
    }

    if not getattr(settings, "TRANSLATION_FAST_MODE", True):
        for endpoint in _libretranslate_endpoints():
            try:
                response = requests.post(
                    endpoint,
                    json=payload,
                    timeout=getattr(settings, "LIBRETRANSLATE_TIMEOUT", 12),
                )
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "application/json" not in content_type.lower():
                    logger.warning("LibreTranslate non-JSON response from %s", endpoint)
                    continue
                data = response.json()

                if isinstance(data, dict) and "translatedText" in data:
                    translated = data["translatedText"]
                    if isinstance(translated, list):
                        return translated
                    return [translated]

                if isinstance(data, list):
                    translated = [item.get("translatedText", "") for item in data]
                    if translated:
                        return translated
            except (requests.RequestException, ValueError, KeyError) as exc:
                logger.warning("LibreTranslate request failed for %s: %s", endpoint, exc)

    # Free backup provider when public LibreTranslate instances are unavailable.
    timeout = getattr(settings, "LIBRETRANSLATE_TIMEOUT", 12)
    workers = getattr(settings, "TRANSLATION_FALLBACK_WORKERS", 8)

    def _translate_one(index, text):
        if not text.strip():
            return index, text

        last_exc = None

        def _from_mymemory(query_text):
            response = requests.get(
                "https://api.mymemory.translated.net/get",
                params={
                    "q": query_text,
                    "langpair": f"{source_lang}|{target_lang}",
                },
                headers={"User-Agent": "AI-Crop-Health-Translator/1.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            translated_text = data.get("responseData", {}).get("translatedText") if isinstance(data, dict) else None
            if translated_text and "MYMEMORY WARNING" not in translated_text:
                return translated_text
            raise RuntimeError("MyMemory quota exceeded")

        def _from_ftapi(query_text):
            response = requests.get(
                "https://ftapi.pythonanywhere.com/translate",
                params={
                    "sl": source_lang,
                    "dl": target_lang,
                    "text": query_text,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            translated_text = data.get("destination-text") if isinstance(data, dict) else None
            if translated_text:
                return translated_text
            raise RuntimeError("ftapi empty response")

        def _from_gtx(query_text):
            response = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": source_lang,
                    "tl": target_lang,
                    "dt": "t",
                    "q": query_text,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                parts = [chunk[0] for chunk in data[0] if isinstance(chunk, list) and chunk]
                translated_text = "".join(parts).strip()
                if translated_text:
                    return translated_text
            raise RuntimeError("gtx empty response")

        # Fastest-first chain for better UX on full-page translation.
        provider_chain = [_from_gtx, _from_ftapi, _from_mymemory]

        for _ in range(1):
            try:
                for provider in provider_chain:
                    try:
                        translated_text = provider(text)
                        return index, translated_text or text
                    except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
                        last_exc = exc
            except Exception as exc:
                last_exc = exc
                time.sleep(0.25)

        raise last_exc

    translated = [""] * len(texts)
    succeeded = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(_translate_one, idx, text): idx
            for idx, text in enumerate(texts)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                resolved_idx, translated_text = future.result()
                translated[resolved_idx] = translated_text
                succeeded += 1
            except (requests.RequestException, ValueError, KeyError) as exc:
                logger.warning("MyMemory translation failed for index %s: %s", idx, exc)
                translated[idx] = texts[idx]

    if succeeded == 0:
        # Last retry as sequential single-thread mode to avoid provider throttling bursts.
        for idx, text in enumerate(texts):
            try:
                _, translated_text = _translate_one(idx, text)
                translated[idx] = translated_text
                succeeded += 1
            except (requests.RequestException, ValueError, KeyError):
                translated[idx] = text

    if succeeded == 0:
        raise RuntimeError("All free translation providers failed")

    return translated


def translate_text(text, target_lang):
    """Translate a single text from English to target language using free providers."""
    translated_list = _translate_batch([text], target_lang)
    return translated_list[0] if translated_list else text


@require_POST
def translate(request):
    """API endpoint: translate one or more text entries and return JSON."""
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    target_lang = (body.get("target_lang") or "en").strip().lower()
    if target_lang not in SUPPORTED_LANGUAGES:
        return JsonResponse({"error": "Unsupported language."}, status=400)

    texts = body.get("texts")
    single_text = body.get("text")

    if isinstance(texts, list):
        incoming_texts = [str(item) for item in texts]
    elif isinstance(single_text, str):
        incoming_texts = [single_text]
    else:
        return JsonResponse({"error": "Provide text or texts."}, status=400)

    trimmed_texts = [text.strip() for text in incoming_texts]
    if not any(trimmed_texts):
        return JsonResponse({"error": "No translatable text provided."}, status=400)

    if target_lang == "en":
        return JsonResponse(
            {
                "target_lang": target_lang,
                "translations": incoming_texts,
                "from_cache": True,
                "fallback": False,
            }
        )

    cache_ttl = getattr(settings, "TRANSLATION_CACHE_TIMEOUT", 60 * 60 * 24)
    translated_output = [""] * len(incoming_texts)
    uncached_positions_by_text = {}

    for index, original in enumerate(incoming_texts):
        if not original.strip():
            translated_output[index] = original
            continue

        cache_key = _make_cache_key(target_lang, original)
        cached_value = cache.get(cache_key)
        if cached_value is not None:
            translated_output[index] = cached_value
        else:
            uncached_positions_by_text.setdefault(original, []).append(index)

    fallback = False
    if uncached_positions_by_text:
        try:
            unique_uncached_texts = list(uncached_positions_by_text.keys())
            translated_uncached = _translate_batch(unique_uncached_texts, target_lang)
            if len(translated_uncached) != len(unique_uncached_texts):
                raise RuntimeError("Unexpected translation response size")

            for source_text, translated in zip(unique_uncached_texts, translated_uncached):
                for position in uncached_positions_by_text[source_text]:
                    translated_output[position] = translated
                cache.set(
                    _make_cache_key(target_lang, source_text),
                    translated,
                    cache_ttl,
                )
        except RuntimeError:
            fallback = True
            translated_output = incoming_texts

    return JsonResponse(
        {
            "target_lang": target_lang,
            "translations": translated_output,
            "from_cache": not bool(uncached_positions_by_text),
            "fallback": fallback,
        }
    )
