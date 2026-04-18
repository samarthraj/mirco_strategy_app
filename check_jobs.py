import os, requests, json, urllib3
urllib3.disable_warnings()

base = os.environ.get('MSTR_BASE_URL', 'https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api')
session = requests.Session()
session.verify = False

resp = session.post(f'{base}/auth/login',
    json={'username': os.environ['MSTR_USERNAME'], 'password': os.environ['MSTR_PASSWORD'], 'loginMode': 1},
    headers={'Content-Type': 'application/json', 'Accept': 'application/json'})

token = resp.headers.get('X-MSTR-AuthToken')
print(f'Login: {resp.status_code}, token: {token[:20] if token else "NONE"}...')
print(f'Cookies: {dict(session.cookies)}')

if not token:
    print(resp.text[:500])
    exit()

headers = {
    'X-MSTR-AuthToken': token,
    'Accept': 'application/json',
}

# Try with project header too
project_id = 'E77B77894C04BF0E6D244F9363CFAF64'

# Try various monitor endpoints to find the right one
endpoints = [
    '/monitors/iServer/jobs',
    '/monitors/jobs',
    '/monitors/iServer/nodes',
    '/monitors/caches/iServer',
    '/monitors/userConnections',
    '/sessions',
    '/status',
]

for ep in endpoints:
    resp = session.get(f'{base}{ep}', headers=headers)
    status = resp.status_code
    preview = resp.text[:200] if status == 200 else resp.text[:100]
    print(f'{status} | GET {ep}')
    if status == 200:
        print(f'  {preview}')
    print()

if resp.status_code == 200:
    data = resp.json()
    if isinstance(data, list):
        print(f'Active jobs: {len(data)}')
        for job in data[:10]:
            print(json.dumps(job, indent=2)[:500])
            print('---')
    elif isinstance(data, dict):
        print(json.dumps(data, indent=2)[:3000])
else:
    print(resp.text[:1000])

requests.post(f'{base}/auth/logout', headers=headers, verify=False)
