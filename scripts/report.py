#!/usr/bin/python3
import os, sys, glob, re, httpx
from datetime import datetime

LOG_DIR = os.path.expanduser('~/techbro/logs')

SZEJAY_TOKEN = ''
hermes_env = os.path.expanduser('~/.hermes/.env')
if os.path.exists(hermes_env):
    with open(hermes_env) as f:
        for line in f:
            if line.strip().startswith('SZEJAY_BOT_TOKEN='):
                SZEJAY_TOKEN = line.strip().split('=', 1)[1]
                break

CHAT_ID = '1022032312'

def get_latest_log():
    logs = sorted(glob.glob(f'{LOG_DIR}/pipeline-*.log'), key=os.path.getmtime, reverse=True)
    return logs[0] if logs else None

def parse_log(path):
    with open(path) as f:
        content = f.read()
    info = dict(time=datetime.now().strftime('%H:%M WIB'), staged=False, posted=False,
                title='', hook='', source='', score=0, error='', fallback=False)
    if re.search(r'Post #\d+ staged', content): info['staged'] = True
    if re.search(r'Thread root: \d+', content): info['posted'] = True
    m = re.search(r'\[(\w+)\] score=(\d+) \| (.+?)$', content, re.M)
    if m: info['source'], info['score'], info['title'] = m.group(1), int(m.group(2)), m.group(3).strip()
    if 'Checking DB' in content: info['fallback'] = True
    m = re.search(r'\[DB\] score=(\d+) \| (.+?)$', content, re.M)
    if m: info['title'], info['score'] = m.group(2).strip(), int(m.group(1))
    m = re.search(r'Hook: (.+?)$', content, re.M)
    if m: info['hook'] = m.group(1).strip()[:80]
    m = re.search(r'ERROR: (.+?)$', content, re.M)
    if m: info['error'] = m.group(1).strip()
    if 'DEDUP' in content and not info['staged']: info['error'] = info['error'] or 'All articles deduped'
    return info

def send_report(info):
    if not SZEJAY_TOKEN:
        print('No bot token, skipping report')
        return
    e, s = ('✅','POSTED') if info['posted'] else ('⏳','STAGED') if info['staged'] else ('❌','FAILED: '+info['error']) if info['error'] else ('⏭️','SKIPPED')
    lines = [f'{e} TechBro Report - {info["time"]}', f'Status: {s}']
    if info['title']: lines += [f'Article: {info["title"][:60]}', f'Source: {info["source"]} | Score: {info["score"]}']
    if info['fallback']: lines.append('Used DB fallback')
    if info['hook']: lines.append(f'Hook: {info["hook"]}')
    r = httpx.post(f'https://api.telegram.org/bot{SZEJAY_TOKEN}/sendMessage', json={'chat_id': CHAT_ID, 'text': chr(10).join(lines)}, timeout=10)
    print('Report sent to @Szejay_bot' if r.status_code == 200 else f'Failed: {r.status_code}')

if __name__ == '__main__':
    log = get_latest_log()
    if not log: print('No logs'); sys.exit(0)
    send_report(parse_log(log))
