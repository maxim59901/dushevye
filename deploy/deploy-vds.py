#!/usr/bin/env python3
"""Deploy a local vds-payload.tgz to the VDS via SSH/SFTP.

The archive sits next to this script and is gitignored (*.tgz).
Password from env DUSHEVYE_VDS_PASS only (not argv). Optional key: DUSHEVYE_VDS_KEY.
"""
import os, sys, tarfile, tempfile, time
from pathlib import Path
import paramiko

HOST = os.environ.get('DUSHEVYE_VDS_HOST', '178.212.15.231')
USER = os.environ.get('DUSHEVYE_VDS_USER', 'root')
PASS = os.environ.get('DUSHEVYE_VDS_PASS')
KEY = os.environ.get('DUSHEVYE_VDS_KEY')
PAYLOAD = Path(__file__).resolve().parent / 'vds-payload.tgz'

def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(hostname=HOST, username=USER, timeout=30, allow_agent=False, look_for_keys=False)
    if KEY:
        kwargs['key_filename'] = KEY
    elif PASS:
        kwargs['password'] = PASS
    else:
        sys.exit('Set DUSHEVYE_VDS_PASS or DUSHEVYE_VDS_KEY')
    c.connect(**kwargs)
    return c

def main():
    if not PAYLOAD.exists():
        sys.exit(f'missing {PAYLOAD}')
    client = connect()
    sftp = client.open_sftp()
    remote_tgz = f'/tmp/dushevye-payload-{int(time.time())}.tgz'
    print('upload', PAYLOAD, '->', remote_tgz)
    sftp.put(str(PAYLOAD), remote_tgz)
    cmds = f'''
set -e
rm -rf /tmp/dushevye-payload-dir
mkdir -p /tmp/dushevye-payload-dir
tar -xzf {remote_tgz} -C /tmp/dushevye-payload-dir
bash /tmp/dushevye-payload-dir/install-on-vds.sh
rm -f {remote_tgz}
'''
    stdin, stdout, stderr = client.exec_command(cmds, get_pty=True)
    out = stdout.read().decode('utf-8', 'replace')
    err = stderr.read().decode('utf-8', 'replace')
    code = stdout.channel.recv_exit_status()
    print(out)
    if err.strip():
        print('STDERR:', err)
    sftp.close(); client.close()
    sys.exit(code)

if __name__ == '__main__':
    main()
