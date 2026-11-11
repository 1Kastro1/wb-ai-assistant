import fcntl, hashlib, importlib.util, json, os, pathlib, shutil, subprocess, time, urllib.request

ROOT=pathlib.Path('/srv/wb-ai-assistant')
TARGET='4d92350593fd8dbe10eb0472f35db0ea197e3419'
KEEP='20261111T103132Z'
BACKUPS=pathlib.Path('/var/backups/wb-ai-assistant')
REPORT=pathlib.Path('/root/backup-retention-install-1111.json')

def run(args,**kw):return subprocess.run(args,cwd=ROOT,check=True,**kw)
def out(args):return subprocess.check_output(args,cwd=ROOT,text=True).strip()
def save(value):REPORT.write_text(json.dumps(value,indent=2))

def main():
    if REPORT.exists():raise RuntimeError('An installation report already exists; inspect before retrying')
    with open('/var/lock/wb-assistant-update.lock','a') as update_lock,open(BACKUPS/'.backup.lock','a') as backup_lock:
        fcntl.flock(update_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(backup_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=subprocess.run(['systemctl','is-active','wb-assistant-backup.service'],capture_output=True,text=True).stdout.strip()
        assert active not in ('active','activating','deactivating'),active
        assert (BACKUPS/'latest-verified').read_text().strip()==KEEP
        text=pathlib.Path('/root/backup-one-prepare.log').read_text()
        approved=json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
        assert approved['keep']==KEEP and len(approved['files'])==33 and approved['remove_bytes']==34163935581
        run(['git','fetch','origin','main'])
        assert out(['git','rev-parse','origin/main'])==TARGET
        old=out(['git','rev-parse','HEAD'])
        run(['git','merge-base','--is-ancestor',old,TARGET])
        run(['git','diff','--exit-code',old,TARGET,'--','backend','frontend','docker-compose.yml'])
        dirty=out(['git','diff','--name-only']).splitlines()
        assert not set(dirty)-{'deploy/backup-vps.sh'},dirty
        history=pathlib.Path('/root/backup-retention-install-1111-files')
        history.mkdir(mode=0o700)
        (history/'previous-local.patch').write_text(out(['git','diff']))
        for path in ('/usr/local/sbin/wb-assistant-backup','/etc/systemd/system/wb-assistant-backup.service','/etc/systemd/system/wb-assistant-backup.timer'):
            source=pathlib.Path(path)
            if source.exists():shutil.copy2(source,history/(source.name+'.before'))
        # The only local change is the old retention block, preserved above.
        run(['git','restore','--','deploy/backup-vps.sh'])
        run(['git','merge','--ff-only','origin/main'])
        spec=importlib.util.spec_from_file_location('retention',ROOT/'deploy/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        volume=json.loads(out(['docker','volume','inspect','wb-ai-assistant_wb_data']))[0]
        data_backups=pathlib.Path(volume['Mountpoint'])/'backups'
        # Revalidate the exact files approved by the user, including the new
        # complete replacement. No newly discovered file may join this cleanup.
        current=retention.plan(BACKUPS,KEEP,data_backups)
        allowed={item['path']:item for item in approved['files']}
        for item in current['files']:
            assert item['path'] in allowed and item==allowed[item['path']],item['path']
        manifest=json.loads((BACKUPS/(KEEP+'.verified.json')).read_text())
        state={'commit':TARGET,'keep':KEEP,'approved_files':33,'planned_files':len(current['files']),
            'planned_bytes':current['remove_bytes'],'backup_bytes':sum(p['bytes'] for p in manifest['parts'].values()),
            'backup_checksums_verified':True,'previous_files':str(history)}
        save(state)
        files=[('deploy/backup_retention.py','/usr/local/lib/wb-ai-assistant/backup_retention.py',0o700),
            ('deploy/backup-vps.sh','/usr/local/sbin/wb-assistant-backup',0o700),
            ('deploy/update-vps.sh','/usr/local/sbin/wb-assistant-update',0o700),
            ('deploy/wb-assistant-backup.service','/etc/systemd/system/wb-assistant-backup.service',0o644),
            ('deploy/wb-assistant-backup.timer','/etc/systemd/system/wb-assistant-backup.timer',0o644)]
        for source,target,mode in files:
            source=ROOT/source;target=pathlib.Path(target)
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.installing')
            shutil.copyfile(source,temporary);temporary.chmod(mode);temporary.replace(target)
            assert hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(target.read_bytes()).digest()
        run(['bash','-n','/usr/local/sbin/wb-assistant-backup','/usr/local/sbin/wb-assistant-update'])
        result=retention.apply(BACKUPS,KEEP,data_backups)
        assert result==current
        state.update(deleted_files=len(result['files']),deleted_bytes=result['remove_bytes'],deleted=result['files'])
        save(state)
        run(['systemctl','daemon-reload'])
        run(['systemctl','enable','--now','wb-assistant-backup.timer'])
        assert out(['systemctl','is-active','wb-assistant-backup.timer'])=='active'
        assert out(['systemctl','is-enabled','wb-assistant-backup.timer'])=='enabled'
        assert '--rotate' in out(['systemctl','show','wb-assistant-backup.service','--property=ExecStart','--value'])
        assert retention.plan(BACKUPS,KEEP,data_backups)['files']==[]
        health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=20))
        assert health['status']=='ok' and health['database']=='ok'
        assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=20).status==200
        state.update(installed=True,timer_active=True,health=health,free_bytes=shutil.disk_usage(ROOT).free)
        save(state)
        print('INSTALLED',flush=True)
        for key in ('keep','deleted_files','deleted_bytes','backup_bytes','free_bytes','timer_active','health'):
            print(key,state[key],flush=True)
        run(['df','-h','/'])

if __name__=='__main__':main()
