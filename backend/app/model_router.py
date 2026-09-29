from .config import MODEL
from .db import Session, setting


class ModelRouter:
    """All task classes stay local. The installed model is reused by default."""
    PROFILES = {'economy':(4096,256),'normal':(8192,768),'quality':(16384,1536)}

    def choose(self, task='STANDARD'):
        with Session() as db:
            mode=setting(db,'performance_mode','normal')
        context,predict=self.PROFILES.get(mode,self.PROFILES['normal'])
        if task=='FAST':predict=min(predict,256)
        return {'model':MODEL,'options':{'num_ctx':context,'num_predict':predict,'temperature':0.2}}
