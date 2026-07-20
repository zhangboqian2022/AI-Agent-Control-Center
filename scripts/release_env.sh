#!/usr/bin/env bash

validate_release_credentials() {
  local codesign_identity="${AACC_CODESIGN_IDENTITY:-}"
  local notary_profile="${AACC_NOTARY_PROFILE:-}"
  if [[ -n "$codesign_identity" && -z "$notary_profile" ]] || \
     [[ -z "$codesign_identity" && -n "$notary_profile" ]]; then
    echo "错误：正式签名需要同时设置 AACC_CODESIGN_IDENTITY 和 AACC_NOTARY_PROFILE" >&2
    return 1
  fi
}
