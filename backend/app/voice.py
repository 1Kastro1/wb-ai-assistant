"""Local Russian speech recognition (Vosk) and speech synthesis (Silero)."""
from __future__ import annotations

from array import array
from io import BytesIO
import json
import re
import threading
import wave

from .config import DATA


MODEL_ROOT = DATA / 'models'
SILERO_MODEL = MODEL_ROOT / 'silero' / 'v5_5_ru.pt'
VOSK_MODEL = MODEL_ROOT / 'vosk' / 'vosk-model-small-ru-0.22'
SAMPLE_RATE = 48_000
SPEAKERS = ('xenia', 'baya', 'kseniya', 'aidar')
_tts_model = None
_stt_model = None
_tts_lock = threading.Lock()
_stt_lock = threading.Lock()


def status():
    try:
        import torch  # noqa: F401
        torch_ready = True
    except ImportError:
        torch_ready = False
    try:
        import vosk  # noqa: F401
        vosk_ready = True
    except ImportError:
        vosk_ready = False
    return {
        'available': torch_ready and vosk_ready and SILERO_MODEL.is_file() and VOSK_MODEL.is_dir(),
        'tts': torch_ready and SILERO_MODEL.is_file(),
        'stt': vosk_ready and VOSK_MODEL.is_dir(),
        'engine': 'Silero TTS + Vosk STT',
        'speakers': list(SPEAKERS),
        'default_speaker': 'aidar',
    }


def _clean_for_speech(text: str) -> str:
    text = re.sub(r'https?://\S+', 'ссылка', text)
    text = re.sub(r'[`*_#>|\[\]{}]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:3000]


def _chunks(text: str, limit: int = 700):
    parts = re.split(r'(?<=[.!?])\s+', text)
    result, current = [], ''
    for part in parts:
        if current and len(current) + len(part) + 1 > limit:
            result.append(current)
            current = part
        else:
            current = f'{current} {part}'.strip()
    if current:
        result.append(current)
    return result


def _load_tts():
    global _tts_model
    if _tts_model is None:
        if not SILERO_MODEL.is_file():
            raise ValueError('Модель Silero TTS не установлена. Запустите update.bat.')
        import torch
        torch.set_num_threads(min(4, max(1, torch.get_num_threads())))
        _tts_model = torch.package.PackageImporter(str(SILERO_MODEL)).load_pickle('tts_models', 'model')
        _tts_model.to(torch.device('cpu'))
    return _tts_model


def synthesize(text: str, speaker: str = 'aidar') -> bytes:
    if speaker not in SPEAKERS:
        raise ValueError('Неизвестный голос Silero')
    cleaned = _clean_for_speech(text)
    if not cleaned:
        raise ValueError('Нет текста для озвучивания')
    with _tts_lock:
        model = _load_tts()
        samples: list[int] = []
        chunks = _chunks(cleaned)
        for index, chunk in enumerate(chunks):
            audio = model.apply_tts(text=chunk, speaker=speaker, sample_rate=SAMPLE_RATE)
            samples.extend(max(-32768, min(32767, round(float(value) * 32767))) for value in audio.tolist())
            if index + 1 < len(chunks):
                samples.extend([0] * int(SAMPLE_RATE * 0.18))
    pcm = array('h', samples)
    output = BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


def _load_stt():
    global _stt_model
    if _stt_model is None:
        if not VOSK_MODEL.is_dir():
            raise ValueError('Русская модель распознавания речи не установлена. Запустите update.bat.')
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        _stt_model = Model(str(VOSK_MODEL))
    return _stt_model


def transcribe(raw: bytes) -> str:
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError('Запись слишком большая')
    try:
        wav = wave.open(BytesIO(raw), 'rb')
    except (wave.Error, EOFError):
        raise ValueError('Не удалось прочитать запись с микрофона') from None
    with wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
            raise ValueError('Нужна запись WAV, моно, PCM 16 бит')
        from vosk import KaldiRecognizer
        with _stt_lock:
            recognizer = KaldiRecognizer(_load_stt(), wav.getframerate())
            while data := wav.readframes(4000):
                recognizer.AcceptWaveform(data)
            text = json.loads(recognizer.FinalResult()).get('text', '').strip()
    if not text:
        raise ValueError('Речь не распознана. Говорите ближе к микрофону и повторите.')
    return text
