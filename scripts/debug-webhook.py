#!/usr/bin/env python3
"""Debug: check what the server sees for webhook auth."""
import hmac, hashlib, json, http.client

secret = '42e7bbce7ede1706f0ebe40ec251619c884d31b01978035b26b96d71a667844c'
body_dict = {
    'request_id': 'adr_debug_webhook',
    'tenant_id': 'default',
    'repository': 'Aftergraph/work-intelligence-v2',
    'ref': 'refs/heads/main',
    'head_sha': 'a33339e9e2a8fca264e9a39388c0a63bcd912e67',
    'event_key': 'push',
    'capability': 'dependency.patch.merge',
    'objective': 'Debug webhook',
    'impact_summary': 'Debug',
    'evidence': [{'kind': 'test', 'url': 'https://x'}],
    'tests_passed': True, 'patch_release': True,
    'changed_files': ['src/api.py'],
    'author_permission_tier': 15, 'test_coverage_delta': 10,
    'critical_path_penalty': 0,
    'auth_or_secret_touched': False, 'proxy_or_ssl_touched': False
}
body = json.dumps(body_dict, separators=(',',':')).encode()
sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
full_sig = f'sha256={sig}'
print(f'body_len={len(body)}')
print(f'sig={full_sig[:30]}...')

conn = http.client.HTTPConnection('172.17.0.1', 8090)
conn.request('POST', '/v1/autonomy/decisions/evaluate', body=body, headers={
    'Content-Type': 'application/json',
    'X-Hub-Signature-256': full_sig,
})
resp = conn.getresponse()
print(f'Status: {resp.status}')
data = resp.read().decode()
print(data[:500])

# Also try with bearer to confirm server is up
print('\n--- bearer sanity check ---')
conn2 = http.client.HTTPConnection('172.17.0.1', 8090)
token = 'gnHzGyXc32ndxUCivJQfK8YaqqM4FeOoNYAF44Eibtf5H-cFcoyuujA-GzvQatBZ'
conn2.request('GET', '/v1/autonomy/decisions/history?limit=1', headers={
    'Authorization': f'Bearer {token}',
})
resp2 = conn2.getresponse()
print(f'Status: {resp2.status}')
print(resp2.read().decode()[:200])
