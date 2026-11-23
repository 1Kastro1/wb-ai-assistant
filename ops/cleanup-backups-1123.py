"""Review/apply an exact assistant-only backup plan; never recurse or follow links."""
import fcntl,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path('/var/backups/wb-ai-assistant')
PLAN=Path('/root/assistant-cleanup-1123-plan.json')
REPORT=Path('/root/assistant-cleanup-1123-result.json')
MODULE=Path('/usr/local/lib/wb-ai-assistant/backup_retention.py')
MODULE_SHA='7c960a389adbf03efab3d84be5265632e5ec46715da596366bf7fd8505353177'
INITIAL_STAMP='20261123T033001Z'
SNAPSHOTS=re.compile(r'(?:rules-before-cleanup-\d{8}T\d{6,12}Z|lamp-reply-before-human-2)\.json')

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def data_folder():
    mount=Path(subprocess.check_output(['docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data'],text=True).strip())
    assert mount.is_absolute() and mount.name=='_data' and mount.parent.name=='wb-ai-assistant_wb_data'
    return mount/'backups'

def inventory(module,keep,data):
    result=module.plan(ROOT,keep,data)
    cutoff=module.stamp_time(keep)
    for path in data.iterdir():
        if not SNAPSHOTS.fullmatch(path.name):continue
        info=module.regular(path)
        assert not path.is_symlink() and path.resolve().parent==data.resolve()
        if info.st_mtime>=cutoff:raise RuntimeError('A newer snapshot must be preserved: '+path.name)
        result['files'].append({'path':str(path),'bytes':info.st_size,'mtime_ns':info.st_mtime_ns,'inode':info.st_ino,'device':info.st_dev})
    result['files']=sorted(result['files'],key=lambda row:row['path'])
    assert len({row['path'] for row in result['files']})==len(result['files'])
    result['remove_bytes']=sum(row['bytes'] for row in result['files'])
    planned={row['path'] for row in result['files']}
    # Do not quietly leave a newer standalone database copy behind or delete it.
    for path in data.iterdir():
        if path.suffix in ('.dump','.db','.sql','.gz','.zip') and str(path) not in planned:
            raise RuntimeError('Unplanned backup needs review: '+path.name)
    return result

def emit(result):
    print(json.dumps(result,indent=2),flush=True)

def main():
    os.umask(0o077)
    assert digest(MODULE)==MODULE_SHA,'Installed retention implementation changed'
    spec=importlib.util.spec_from_file_location('retention',MODULE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with open('/var/lock/wb-assistant-update.lock','a') as update,open(ROOT/'.backup.lock','a') as backup:
        fcntl.flock(update,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(backup,fcntl.LOCK_EX|fcntl.LOCK_NB)
        keep=(ROOT/'latest-verified').read_text().strip()
        assert keep>INITIAL_STAMP,'The newly prepared full copy is not ready'
        data=data_folder();module.root_path(data)
        current=inventory(module,keep,data)
        manifest=json.loads((ROOT/(keep+'.verified.json')).read_text())
        kept_bytes=sum(part['bytes'] for part in manifest['parts'].values())
        initial_free=int(sys.argv[2]) if sys.argv[1]=='plan' else json.loads(PLAN.read_text())['initial_free_bytes']
        if sys.argv[1]=='plan':
            current.update(initial_free_bytes=initial_free,keep_bytes=kept_bytes,prepared_at=time.time(),data_backups=str(data))
            PLAN.write_text(json.dumps(current,indent=2))
            emit({'keep':keep,'keep_bytes':kept_bytes,'files':len(current['files']),'remove_bytes':current['remove_bytes'],
                'free_now_bytes':shutil.disk_usage(ROOT).free,'plan_sha256':digest(PLAN)})
            return
        assert sys.argv[1]=='apply' and len(sys.argv)==3
        assert not REPORT.exists(),'A cleanup result already exists; inspect before retrying'
        assert digest(PLAN)==sys.argv[2],'Reviewed plan changed'
        approved=json.loads(PLAN.read_text())
        assert approved['keep']==keep and approved['data_backups']==str(data)
        assert current['files']==approved['files'] and current['remove_bytes']==approved['remove_bytes'],'Backup inventory changed; prepare a new review'
        free_before=shutil.disk_usage(ROOT).free
        # Validate the entire exact plan before the first irreversible action.
        for row in approved['files']:
            path=Path(row['path']);info=module.regular(path)
            assert (info.st_size,info.st_mtime_ns,info.st_ino,info.st_dev)==(row['bytes'],row['mtime_ns'],row['inode'],row['device'])
            assert path.resolve().parent in (ROOT.resolve(),data.resolve()) or (path.name=='postgres.dump' and path.parent.parent.resolve()==ROOT.resolve())
        for row in approved['files']:Path(row['path']).unlink()
        remaining=inventory(module,keep,data)
        assert not remaining['files']
        assert (ROOT/'latest-verified').read_text().strip()==keep
        free_after=shutil.disk_usage(ROOT).free
        result={'keep':keep,'keep_bytes':kept_bytes,'deleted_files':len(approved['files']),'deleted_bytes':approved['remove_bytes'],
            'initial_free_bytes':initial_free,'free_before_deletion_bytes':free_before,'free_after_bytes':free_after,
            'net_free_change_bytes':free_after-initial_free,'deletion_free_change_bytes':free_after-free_before,
            'remaining_old_backups':0,'deleted':approved['files']}
        REPORT.write_text(json.dumps(result,indent=2))
        try:
            health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=20))
            assert health.get('status')=='ok' and health.get('database')=='ok'
            result['health']=health
        except Exception as error:result['health_check_error']=type(error).__name__
        REPORT.write_text(json.dumps(result,indent=2));emit({k:v for k,v in result.items() if k!='deleted'})

if __name__=='__main__':main()
