#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Host firewall for the CI runner network (run as root; idempotent). Traffic from the runner's
# bridge (br-oa-ci) may go to the internet but never to the tailnet, the host's LAN or other
# private ranges. Rules live in DOCKER-USER, which Docker evaluates before its own rules.
# Usage: sudo ./firewall.sh [apply|remove|status]
set -euo pipefail
BR=br-oa-ci
V4_BLOCK=(100.64.0.0/10 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16)
V6_BLOCK=(fd7a:115c:a1e0::/48 fc00::/7 fe80::/10)

rule() {  # tool action dst
    local t=$1 a=$2 d=$3
    case $a in
        apply) $t -C DOCKER-USER -i "$BR" -d "$d" -j REJECT 2>/dev/null \
                   || $t -I DOCKER-USER -i "$BR" -d "$d" -j REJECT ;;
        remove) while $t -D DOCKER-USER -i "$BR" -d "$d" -j REJECT 2>/dev/null; do :; done ;;
    esac
}

case ${1:-apply} in
    apply|remove)
        for d in "${V4_BLOCK[@]}"; do rule iptables "$1" "$d"; done
        if ip6tables -L DOCKER-USER >/dev/null 2>&1; then
            for d in "${V6_BLOCK[@]}"; do rule ip6tables "$1" "$d"; done
        fi ;;
    status) iptables -S DOCKER-USER | grep -- "-i $BR" || echo "no rules for $BR" ;;
    *) echo "usage: $0 [apply|remove|status]" >&2; exit 2 ;;
esac
