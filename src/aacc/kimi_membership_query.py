"""Same-origin Kimi membership query shared by native WebView and Edge CDP."""

from __future__ import annotations

import json


def membership_fetch_expression(generation: int = 0) -> str:
    """Return a promise expression whose result contains no browser credential."""

    base = "/apiv2/kimi.gateway.membership.v2.MembershipService/"
    return f"""
(async () => {{
  const generation = {generation};
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 15000);
  const request = async (method) => {{
    let accessToken = localStorage.getItem('access_token');
    if (accessToken) {{
      try {{
        const parsed = JSON.parse(accessToken);
        if (typeof parsed === 'string') accessToken = parsed;
      }} catch (_) {{}}
    }}
    if (!accessToken) throw new Error('UNAUTHORIZED:NO_TOKEN');
    const response = await fetch({json.dumps(base)} + method, {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'Connect-Protocol-Version': '1',
        'Authorization': 'Bearer ' + accessToken
      }},
      credentials: 'include',
      signal: controller.signal,
      body: '{{}}'
    }});
    if (response.status === 401 || response.status === 403) {{
      throw new Error('UNAUTHORIZED:' + response.status);
    }}
    if (!response.ok) throw new Error('HTTP:' + response.status);
    return await response.json();
  }};
  const load = async () => {{
    return await Promise.all([
      request('GetSubscriptionStats'),
      request('GetSubscription')
    ]);
  }};
  try {{
    const [stats, subscription] = await load();
    return {{kind: 'quota', generation, stats, subscription}};
  }} catch (error) {{
    controller.abort();
    const message = String(error && error.message || error);
    return {{
      kind: message.startsWith('UNAUTHORIZED:') ? 'unauthorized' : 'error',
      generation
    }};
  }} finally {{
    clearTimeout(deadline);
  }}
}})()
"""
