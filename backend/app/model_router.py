from .config import MODEL
from .db import Session, setting


class ModelRouter:
    """All task classes stay local. The installed model is reused by default."""
    PROFILES = {'economy':(2048,256),'normal':(4096,768),'quality':(8192,1536)}

    def choose(self, task='STANDARD'):
        # Policy verification has a fixed profile. Keep it independent of an
        # extra DB connection (previews may use their own isolated DB session).
        if task=='OWNER_RULES':
            # OllamaProvider grows this to the measured request size, up to
            # 16K. Small checks need not allocate the maximum KV context.
            return {'model':MODEL,'options':{'num_ctx':4096,'num_predict':512,'temperature':0.2}}
        with Session() as db:
            mode=setting(db,'performance_mode','normal')
        context,predict=self.PROFILES.get(mode,self.PROFILES['normal'])
        if task=='FAST':predict=min(predict,256)
        return {'model':MODEL,'options':{'num_ctx':context,'num_predict':predict,'temperature':0.2}}

