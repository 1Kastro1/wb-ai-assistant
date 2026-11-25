from .config import MODEL, SUPERVISOR_MODEL
from .db import Session, setting


class ModelRouter:
    """All task classes stay local. The installed model is reused by default."""
    PROFILES = {'economy':(2048,256),'normal':(4096,768),'quality':(8192,1536)}

    @staticmethod
    def supervisor_available():
        return bool(SUPERVISOR_MODEL and ':' in SUPERVISOR_MODEL and '/' not in SUPERVISOR_MODEL
                    and 'cloud' not in SUPERVISOR_MODEL.casefold()
                    and SUPERVISOR_MODEL.split(':')[0].casefold() != MODEL.split(':')[0].casefold())

    def choose(self, task='STANDARD'):
        if task == 'REPLY_SUPERVISOR':
            if not self.supervisor_available():
                raise ValueError('Для независимой проверки требуется отдельная модель Ollama')
            return {'model': SUPERVISOR_MODEL,
                    'options': {'num_ctx': 4096, 'num_predict': 256, 'temperature': 0}}
        # Policy verification has a fixed profile. Keep it independent of an
        # extra DB connection (previews may use their own isolated DB session).
        if task=='OWNER_RULES':
            # Keep one runner size across the whole policy plan. Changing
            # windows between groups evicts the shared evidence prefix.
            return {'model':MODEL,'options':{'num_ctx':16384,'num_predict':512,'temperature':0.2}}
        with Session() as db:
            mode=setting(db,'performance_mode','normal')
        context,predict=self.PROFILES.get(mode,self.PROFILES['normal'])
        if task=='FAST':predict=min(predict,256)
        return {'model':MODEL,'options':{'num_ctx':context,'num_predict':predict,'temperature':0.2}}

