from io import BytesIO
import wave


def test_voice_status(client):
    response=client.get('/voice/status')
    assert response.status_code==200
    assert response.json()['engine']=='Silero TTS + Vosk STT'


def test_voice_tts_returns_wav(client,monkeypatch):
    from app import voice
    monkeypatch.setattr(voice,'synthesize',lambda text,speaker: b'RIFF-test-wave')
    response=client.post('/voice/tts',json={'text':'Привет','speaker':'xenia'})
    assert response.status_code==200
    assert response.headers['content-type']=='audio/wav'
    assert response.content.startswith(b'RIFF')


def test_voice_stt_returns_text(client,monkeypatch):
    from app import voice
    monkeypatch.setattr(voice,'transcribe',lambda raw: 'покажи отзывы')
    buffer=BytesIO()
    with wave.open(buffer,'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*100)
    response=client.post('/voice/stt',files={'audio':('voice.wav',buffer.getvalue(),'audio/wav')})
    assert response.status_code==200
    assert response.json()=={'text':'покажи отзывы'}
