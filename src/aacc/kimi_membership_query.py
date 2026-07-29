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
  const finiteNumber = (value) => {{
    if (typeof value === 'string' && value.trim() === '') return null;
    const number = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(number) ? number : null;
  }};
  const safePercentage = (value) => {{
    if (value && typeof value === 'object') {{
      for (const key of [
        'usedRatio', 'amountUsedRatio', 'usedPercent',
        'percentage', 'ratio', 'value'
      ]) {{
        if (Object.hasOwn(value, key)) {{
          value = value[key];
          break;
        }}
      }}
    }}
    let number = finiteNumber(value);
    if (number === null || number < 0) return null;
    if (number <= 1) number *= 100;
    if (number > 100) return null;
    return Math.round(number);
  }};
  const safeReset = (value) => {{
    if (!value || typeof value !== 'object') return null;
    let reset = null;
    for (const key of [
      'resetTime', 'resetAt', 'expireTime', 'expiresAt', 'nextResetTime'
    ]) {{
      if (Object.hasOwn(value, key)) {{
        reset = value[key];
        break;
      }}
    }}
    if (reset && typeof reset === 'object') reset = reset.seconds;
    const number = finiteNumber(reset);
    if (number !== null && number >= 0) return number;
    if (
      typeof reset === 'string' &&
      reset.length <= 40 &&
      /^\\d{{4}}-\\d{{2}}-\\d{{2}}T/.test(reset)
    ) return reset;
    return null;
  }};
  const safeWindow = (value) => {{
    const percentage = safePercentage(value);
    const resetTime = safeReset(value);
    if (percentage === null && resetTime === null) return null;
    return {{percentage, resetTime}};
  }};
  const safeMembership = (source) => {{
    if (!source || typeof source !== 'object') return {{}};
    const candidates = [
      source.subscription,
      source.activeSubscription,
      ...(Array.isArray(source.subscriptions) ? source.subscriptions : []),
      ...(Array.isArray(source.items) ? source.items : []),
      source
    ].filter((item) => item && typeof item === 'object');
    const active = candidates.find(
      (item) => typeof item.status === 'string' &&
        item.status.toUpperCase().includes('ACTIVE')
    ) || candidates[0];
    if (!active) return {{}};
    const level = active.level || active.membershipLevel || active.planType;
    return typeof level === 'string' && level.length <= 64
      ? {{membershipLevel: level}}
      : {{}};
  }};
  const load = async () => {{
    return await Promise.all([
      request('GetSubscriptionStats'),
      request('GetSubscription')
    ]);
  }};
  try {{
    const [stats, subscription] = await load();
    const safeStats = {{
      subscriptionBalance: safeWindow(stats && stats.subscriptionBalance),
      ratelimitCode5h: safeWindow(stats && stats.ratelimitCode5h),
      ratelimitCode7d: safeWindow(stats && stats.ratelimitCode7d)
    }};
    const safeSubscription = safeMembership(subscription);
    return {{
      kind: 'quota',
      generation,
      stats: safeStats,
      subscription: safeSubscription
    }};
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
