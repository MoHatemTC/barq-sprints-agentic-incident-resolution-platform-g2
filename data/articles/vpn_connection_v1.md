---
article_id: KB-S002
title: "VPN client fails to connect"
category: network
service: vpn
workflow_state: published
version: 1
security_level: internal
---

## Symptom
VPN client shows "connection failed" or times out.

## Resolution
1. Confirm active internet connection.
2. Update VPN client to latest version.
3. Clear VPN client cache: `vpnclient --clear-cache`
4. Reconnect.

## Related error codes
- ERR_VPN_TIMEOUT_017