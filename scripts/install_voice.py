"""Download pinned public voice models into the private application data folder."""
from pathlib import Path
import argparse
import shutil
import tempfile
import urllib.request
import zipfile


SILERO_URL = 'https://models.silero.ai/models/tts/ru/v5_5_ru.pt'
VOSK_URL = 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip'


def download(url: str, target: Path, minimum: int, maximum: int):
    if target.exists() and target.stat().st_size >= minimum:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + '.download')
    print(f'Downloading {url}')
    with urllib.request.urlopen(url, timeout=120) as response, temp.open('wb') as output:
        shutil.copyfileobj(response, output)
    size = temp.stat().st_size
    if not minimum <= size <= maximum:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f'Unexpected model size: {size}')
    temp.replace(target)


def safe_extract(archive: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            resolved = (destination / member.filename).resolve()
            if root not in resolved.parents and resolved != root:
                raise RuntimeError('Unsafe path in voice model archive')
        zipped.extractall(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-root', required=True)
    args = parser.parse_args()
    models = Path(args.data_root).resolve() / 'models'
    silero = models / 'silero' / 'v5_5_ru.pt'
    vosk_dir = models / 'vosk' / 'vosk-model-small-ru-0.22'
    download(SILERO_URL, silero, 20_000_000, 300_000_000)
    if not vosk_dir.is_dir():
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'vosk.zip'
            download(VOSK_URL, archive, 30_000_000, 150_000_000)
            safe_extract(archive, models / 'vosk')
    print('Voice models are ready.')


if __name__ == '__main__':
    main()
