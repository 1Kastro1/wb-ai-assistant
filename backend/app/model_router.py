from .config import MODEL
from .db import Session, setting


class ModelRouter:
    """All task classes stay local. The installed model is reused by default."""
    PROFILES = {'economy':(2048,256),'normal':(4096,768),'quality':(8192,1536)}

    def choose(self, task='STANDARD'):
        with Session() as db:
            mode=setting(db,'performance_mode','normal')
        context,predict=self.PROFILES.get(mode,self.PROFILES['normal'])
        if task=='FAST':predict=min(predict,256)
        # A policy check contains all matched owner rules and may return a full
        # repaired answer. The short FAST JSON cap can truncate that answer.
        if task=='OWNER_RULES':context,predict=max(context,8192),max(predict,1024)
        return {'model':MODEL,'options':{'num_ctx':context,'num_predict':predict,'temperature':0.2}}
