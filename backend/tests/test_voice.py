from io import BytesIO
import wave


def test_voice_status(client):
    response=client.get('/voice/status')
    assert response.status_code==200
    assert response.json()['engine']=='Silero TTS + Vosk STT'
    assert response.json()['default_speaker']=='aidar'


def test_voice_tts_returns_wav(client,monkeypatch):
    from app import voice
    selected=[]
    monkeypatch.setattr(voice,'synthesize',lambda text,speaker: selected.append(speaker) or b'RIFF-test-wave')
    response=client.post('/voice/tts',json={'text':'Привет'})
    assert response.status_code==200
    assert response.headers['content-type']=='audio/wav'
    assert response.content.startswith(b'RIFF')
    assert selected==['aidar']


def test_voice_stt_returns_text(client,monkeypatch):
    from app import voice
    monkeypatch.setattr(voice,'transcribe',lambda raw: 'покажи отзывы')
    buffer=BytesIO()
    with wave.open(buffer,'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*100)
    response=client.post('/voice/stt',files={'audio':('voice.wav',buffer.getvalue(),'audio/wav')})
    assert response.status_code==200
    assert response.json()=={'text':'покажи отзывы'}


def test_voice_wake_uses_limited_grammar(client,monkeypatch):
    from app import voice
    received=[]
    monkeypatch.setattr(voice,'transcribe',lambda raw,grammar: received.append(grammar) or 'брат')
    buffer=BytesIO()
    with wave.open(buffer,'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*100)
    response=client.post('/voice/wake',data={'wake_word':'Брат'},files={'audio':('wake.wav',buffer.getvalue(),'audio/wav')})
    assert response.status_code==200
    assert response.json()=={'text':'брат'}
    assert received==[['брат','[unk]']]
