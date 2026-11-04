#!/usr/bin/env python3
"""Run the actual Android menu helper against fake HAL/GPT/hash commands only."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
FAKE = r'''
#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
name=Path(sys.argv[0]).name;args=sys.argv[1:];root=Path(os.environ['FAKE_MENU_ROOT']);failure=os.environ.get('FAKE_MENU_FAILURE','')
with (root/'commands.jsonl').open('a') as out:out.write(json.dumps([name,*args])+'\n')
state=root/'active';active=state.read_text().strip()
if name=='id':print('2000' if failure=='nonroot' else '0')
elif name=='stat':print('0:644' if failure=='permissions' else '0:600')
elif name=='getprop':
 values={'ro.product.device':'Spacewar','sys.boot_completed':'1','ro.boot.slot_suffix':'_a'}
 if failure=='device':values['ro.product.device']='redwood'
 if failure=='incomplete':values['sys.boot_completed']='0'
 if failure=='suffix':values['ro.boot.slot_suffix']='_b'
 print(values[args[0]])
elif name=='bootctl':
 op=args[0]
 if op=='get-number-slots':print('3' if failure=='slots' else '2')
 elif op=='get-current-slot':print('1' if failure=='current' else '0')
 elif op=='is-slot-bootable':sys.exit(1 if failure=='unbootable' else 0)
 elif op=='is-slot-marked-successful':sys.exit(1 if failure=='unsuccessful' else 0)
 elif op=='get-snapshot-merge-status':print('MergeStatus::MERGING' if failure=='snapshot' else 'MergeStatus::NONE')
 elif op=='get-active-boot-slot':print('0' if failure=='readback' else active)
 elif op=='set-active-boot-slot':
  assert args==['set-active-boot-slot','1']
  if failure=='hal':sys.exit(1)
  state.write_text('1')
 else:raise AssertionError(args)
elif name=='blockdev':print(('512' if failure=='sector' else '4096') if args[0]=='--getss' else ('1' if failure=='size' else '100663296'))
elif name=='readlink':print('/dev/block/sde11' if args[-1].endswith('boot_a') else '/dev/block/sdf35' if failure=='mapping' else '/dev/block/sde35')
elif name=='sha256sum':print(('b' if failure=='hash' else 'a')*64+'  /dev/block/by-name/boot_b')
elif name=='sgdisk':
 if args[0]=='--verify':
  summary='No problems found. 1113067 free sectors (4.2 GiB) available in 1\nsegments, the largest of which is 1113067 (4.2 GiB) in size.'
  factory="\nWarning: There is a gap between the main partition table (ending sector 4)\nand the first usable sector (6). This is helpful in some exotic configurations,\nbut is unusual. The util-linux fdisk program often creates disks like this.\nUsing 'j' on the experts' menu can adjust this gap.\n\nWarning: The size of the partition table (12288 bytes) is less than the minimum\nrequired by the GPT specification. Most OSes and tools seem to work fine on\nsuch disks, but this is a violation of the GPT specification and so may cause\nproblems.\n\nNo problems found. 1113067 free sectors (4.2 GiB) available in 1\nsegments, the largest of which is 1113067 (4.2 GiB) in size.\n"
  if failure=='gpt-crc':print('Problem: header CRC invalid')
  elif failure.startswith('factory'):
   text=factory
   if failure=='factory-mutated-gap':text=text.replace('ending sector 4','ending sector 5')
   if failure=='factory-mutated-size':text=text.replace('12288 bytes','16384 bytes')
   if failure=='factory-mutated-word':text=text.replace('is unusual','is acceptable')
   if failure=='factory-extra-warning':text+='\nWarning: recovered backup GPT after corruption\n'
   if failure=='factory-duplicate':text=text.replace('\nNo problems found.','\n'+factory.split('\n\n')[1]+'\n\nNo problems found.')
   if failure=='factory-recovery':text+='\nCaution: main GPT recovered in memory\n'
   print(text)
  else:print(summary)
  if failure=='gpt-warning':print('Warning: invalid primary GPT, recovered in memory',file=sys.stderr)
 elif args[0]=='--print':
  print('Disk identifier (GUID): '+('d' if failure=='disk-guid' else '1')*8+'-1111-1111-1111-111111111111')
  print('Partition table holds up to '+('97' if failure=='factory-count' else '96')+' entries')
  print('Main partition table begins at sector 2 and ends at sector '+('5' if failure=='factory-end' else '4'))
  print('First usable sector is '+('7' if failure=='factory-first' else '6')+', last usable sector is '+('1572857' if failure=='factory-last' else '1572858'))
 elif args[0].startswith('--info='):
  i=int(args[0].split('=')[1]);assert i in (11,35);b=i==35
  guid=('3' if b else '2')*8+('-3333-3333-3333-333333333333' if b else '-2222-2222-2222-222222222222')
  if failure=='boot-guid' and b:guid='44444444-4444-4444-4444-444444444444'
  first=236498 if b else 86022;last=first+24576-1
  if failure=='bounds' and b:last+=1
  label='boot_b' if b else 'boot_a'
  if failure=='label' and b:label='vendor_boot_b'
  print(f"Partition unique GUID: {guid}\nFirst sector: {first} (text)\nLast sector: {last} (text)\nPartition name: '{label}'")
 else:raise AssertionError(args)
elif name=='reboot':pass
elif name=='sleep':pass
else:raise AssertionError(name)
'''.lstrip()
IDENTITY = '''FORMAT_VERSION=1
DEVICE=spacewar
ANDROID_SLOT=0
MENU_SLOT=1
BOOT_B_NODE=/dev/block/by-name/boot_b
BOOT_B_BYTES=100663296
BOOT_B_SHA256={hash}
DISK_GUID=11111111-1111-1111-1111-111111111111
BOOT_A_GUID=22222222-2222-2222-2222-222222222222
BOOT_B_GUID=33333333-3333-3333-3333-333333333333
GPT_DISK_NODE=/dev/block/sde
GPT_BLOCK_SIZE=4096
GPT_ENTRY_COUNT=96
GPT_ENTRIES_LBA=2
GPT_FIRST_USABLE_LBA=6
GPT_LAST_USABLE_LBA=1572858
BOOT_A_GPT_INDEX=11
BOOT_B_GPT_INDEX=35
BOOT_A_FIRST_LBA=86022
BOOT_B_FIRST_LBA=236498
BOOT_PARTITION_BLOCKS=24576
'''.format(hash='a'*64)


def main():
    cases = 0
    with tempfile.TemporaryDirectory(prefix='spacewar-menu-shell-') as temporary:
        work = Path(temporary)
        commands = work/'bin'
        commands.mkdir()
        fake = work/'fake.py'
        fake.write_text(FAKE)
        fake.chmod(0o700)
        for name in ('id','stat','getprop','bootctl','blockdev','readlink','sha256sum','sgdisk','reboot','sleep'):
            (commands/name).symlink_to(fake)
        # Substitute command installation paths only; the production helper's
        # validation and HAL/reboot control flow are executed unchanged.
        helper = work/'menu.sh'
        source = (ROOT/'spacewar-boot-menu.sh').read_text()
        source = source.replace('PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/magisk', f'PATH={commands}:/usr/bin:/bin')
        source = source.replace('BOOTCTL=/data/adb/magisk/bootctl', f'BOOTCTL={commands}/bootctl')
        source = source.replace('SGDISK=/system/bin/sgdisk', f'SGDISK={commands}/sgdisk')
        identity = work/'identity.env'
        source = source.replace('IDENTITY=/data/adb/spacewar-boot-menu/identity.env', f'IDENTITY={identity}')
        helper.write_text(source)

        def run(failure='', mode='--prepare-next-boot', text=IDENTITY, active='0'):
            nonlocal cases
            (work/'active').write_text(active)
            (work/'commands.jsonl').write_text('')
            identity.write_text(text)
            identity.chmod(0o600)
            result = subprocess.run(['/bin/sh',str(helper),mode,'--identity',str(identity)],
                                    capture_output=True,text=True,
                                    env={**os.environ,'FAKE_MENU_ROOT':str(work),'FAKE_MENU_FAILURE':failure})
            import json
            calls=[json.loads(line) for line in (work/'commands.jsonl').read_text().splitlines()]
            changed=[call for call in calls if call[0]=='bootctl' and call[1]=='set-active-boot-slot']
            rebooted=[call for call in calls if call[0]=='reboot']
            cases += 1
            return result,changed,rebooted

        result,changed,rebooted=run(mode='--check')
        assert result.returncode==0 and not changed and not rebooted and (work/'active').read_text()=='0',result.stderr
        result,changed,rebooted=run()
        assert result.returncode==0 and changed==[['bootctl','set-active-boot-slot','1']] and not rebooted,result.stderr
        result,changed,rebooted=run(mode='--reboot-menu')
        assert result.returncode==0 and changed and len(rebooted)==1,result.stderr
        result,changed,rebooted=run(active='1')
        assert result.returncode==0 and not changed and not rebooted,result.stderr
        for failure in ('nonroot','permissions','device','incomplete','suffix','slots','current','unbootable','unsuccessful',
                        'snapshot','sector','size','mapping','gpt-crc','gpt-warning','disk-guid','boot-guid','bounds','label','hash'):
            result,changed,rebooted=run(failure=failure)
            assert result.returncode!=0 and not changed and not rebooted,(failure,result.stdout,result.stderr)
        for text in ('', IDENTITY+'MENU_SLOT=1\n',IDENTITY+'UNKNOWN=1\n',IDENTITY.replace('MENU_SLOT=1','MENU_SLOT=0'),
                     IDENTITY.replace('a'*64,'$(reboot)'),IDENTITY.replace('BOOT_B_GPT_INDEX=35','BOOT_B_GPT_INDEX=999999999999999999999'),
                     IDENTITY.replace('33333333-3333-3333-3333-333333333333','22222222-2222-2222-2222-222222222222')):
            result,changed,rebooted=run(text=text)
            assert result.returncode!=0 and not changed and not rebooted,(text,result.stderr)
        # Exact factory diagnostics are allowed only with pinned GPT geometry.
        result,changed,rebooted=run(failure='factory',mode='--check')
        assert result.returncode==0 and not changed and not rebooted,result.stderr
        for failure in ('factory-mutated-gap','factory-mutated-size','factory-mutated-word',
                        'factory-extra-warning','factory-duplicate','factory-recovery',
                        'factory-count','factory-end','factory-first','factory-last'):
            result,changed,rebooted=run(failure=failure,mode='--check')
            assert result.returncode!=0 and not changed and not rebooted,(failure,result.stderr)
        for field,value in (('GPT_ENTRY_COUNT','97'),('GPT_ENTRIES_LBA','3'),
                            ('GPT_FIRST_USABLE_LBA','7'),('GPT_LAST_USABLE_LBA','1572857')):
            text=IDENTITY.replace(field+'='+next(line.split('=',1)[1] for line in IDENTITY.splitlines() if line.startswith(field+'=')),field+'='+value)
            result,changed,rebooted=run(failure='factory',mode='--check',text=text)
            assert result.returncode!=0 and not changed and not rebooted,(field,result.stderr)
        for failure in ('hal','readback'):
            result,changed,rebooted=run(failure=failure,mode='--reboot-menu')
            assert result.returncode!=0 and changed and not rebooted,(failure,result.stderr)
        # The optional service executes prepare mode after boot completion.
        (work/'active').write_text('0')
        (work/'commands.jsonl').write_text('')
        identity.write_text(IDENTITY)
        service_source=(ROOT/'spacewar-boot-menu-service.sh').read_text()
        service_source=service_source.replace('PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/magisk',f'PATH={commands}:/usr/bin:/bin')
        service_source=service_source.replace('exec /system/bin/sh /data/adb/spacewar-boot-menu/spacewar-boot-menu.sh',f'exec /bin/sh {helper}')
        service=work/'service.sh'
        service.write_text(service_source)
        result=subprocess.run(['/bin/sh',str(service)],capture_output=True,text=True,
                              env={**os.environ,'FAKE_MENU_ROOT':str(work),'FAKE_MENU_FAILURE':''})
        import json
        service_calls=[json.loads(line) for line in (work/'commands.jsonl').read_text().splitlines()]
        assert result.returncode==0 and (work/'active').read_text()=='1' and not any(call[0]=='reboot' for call in service_calls),result.stderr
        cases+=1
        # Missing default identity also remains inert.
        (work/'commands.jsonl').write_text('')
        identity.unlink()
        result=subprocess.run(['/bin/sh',str(helper),'--prepare-next-boot','--identity',str(identity)],capture_output=True)
        assert result.returncode!=0
        cases+=1
    print(f'PASS: {cases} actual-shell cases; identity/HAL/GPT/hash guards; next-B preparation stays in Android; reboot only explicit')


if __name__=='__main__':
    main()
