#!/usr/bin/env bash
set -euo pipefail

# Asserts the monitoring stack actually monitors (A-4, Part 5 audit
# 2026-08-25): that Prometheus is really scraping pokeproxy and that the
# app's alert rules loaded without errors. Goes through Grafana's
# datasource proxy (not a direct Prometheus port-forward or ingress route)
# so this doesn't reopen Part 4's A-3 decision to keep Prometheus off the
# Ingress unauthenticated. `helm upgrade --wait` alone only proves pods
# came up ready -- it proved nothing about scraping in Part 4's own two
# past incidents (a permanently-down Grafana scrape target, silently-empty
# dashboard panels), which is exactly the class of bug this exists to catch.

KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-pokeproxy}"
NAMESPACE="${NAMESPACE:-pokeproxy}"
RELEASE="${RELEASE:-pokeproxy}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:8080/grafana}"
PROM_PROXY="$GRAFANA_URL/api/datasources/proxy/uid/prometheus/api/v1"
RETRY_SECONDS=90
POLL_INTERVAL=5

fail() {
  echo "monitoring-health: FAIL: $1" >&2
  exit 1
}

# Read the admin password from the Secret the Grafana chart itself writes,
# not a literal copied from deploy/monitoring/values.yaml -- a future
# rotation of `grafana.adminPassword` there would otherwise silently break
# this script (and `curl -f` masks a 401 as an opaque failure, not "wrong
# password").
grafana_admin_password="$(kubectl --context "$KUBE_CONTEXT" get secret kube-prometheus-stack-grafana \
  -n "$MONITORING_NAMESPACE" -o jsonpath='{.data.admin-password}' | base64 -d)"
[[ -n "$grafana_admin_password" ]] || fail "could not read the Grafana admin password from the kube-prometheus-stack-grafana Secret"
GRAFANA_AUTH="admin:${grafana_admin_password}"

# Read the *declared* replica count from the Helm release, not the live
# Deployment's .spec.replicas -- an out-of-band `kubectl scale` (exactly
# the kind of drift this check exists to catch) changes the latter but not
# the former, so reading live state here would make the assertion trivially
# pass against the very failure it's meant to detect.
expected_replicas="$(helm get values "$RELEASE" -n "$NAMESPACE" --kube-context "$KUBE_CONTEXT" -a -o json | \
  jq -r '.components.pokeproxy.replicaCount')"
[[ -n "$expected_replicas" && "$expected_replicas" != "null" ]] || \
  fail "could not read components.pokeproxy.replicaCount from the '$RELEASE' Helm release"

echo "==> monitoring-health: waiting for $expected_replicas pokeproxy target(s) up in Prometheus (up to ${RETRY_SECONDS}s -- scrape interval is 15s)"
deadline=$(( $(date +%s) + RETRY_SECONDS ))
up_count=0
last_response=""
while (( $(date +%s) < deadline )); do
  last_response="$(curl -sf -u "$GRAFANA_AUTH" "$PROM_PROXY/query" --data-urlencode 'query=up{job="pokeproxy"}' || true)"
  up_count="$(echo "$last_response" | jq '[.data.result[]? | select(.value[1]=="1")] | length' 2>/dev/null || echo 0)"
  [[ "$up_count" -ge "$expected_replicas" ]] && break
  sleep "$POLL_INTERVAL"
done
[[ "$up_count" -ge "$expected_replicas" ]] || \
  fail "expected $expected_replicas pokeproxy target(s) up after ${RETRY_SECONDS}s, got $up_count (last response: $last_response)"
echo "    OK: $up_count/$expected_replicas pokeproxy target(s) up"

echo "==> monitoring-health: checking PokeProxy* alert rules loaded without errors"
rules_result="$(curl -sf -u "$GRAFANA_AUTH" "$PROM_PROXY/rules")" || \
  fail "could not fetch alert rules through the Grafana datasource proxy"

poke_rule_count="$(echo "$rules_result" | jq '[.data.groups[].rules[] | select(.name | startswith("PokeProxy"))] | length')"
[[ "$poke_rule_count" -eq 3 ]] || fail "expected 3 PokeProxy* alert rules loaded, found $poke_rule_count"

bad_rules="$(echo "$rules_result" | jq -r '[.data.groups[].rules[] | select(.name | startswith("PokeProxy")) | select(.health != "ok")] | .[].name')"
[[ -z "$bad_rules" ]] || fail "PokeProxy* alert rule(s) not health=ok: $bad_rules"
echo "    OK: 3/3 PokeProxy* alert rules health=ok"

echo "==> monitoring-health: checking Grafana is reachable through the ingress"
grafana_status="$(curl -s -o /dev/null -w '%{http_code}' "$GRAFANA_URL/api/health")"
[[ "$grafana_status" == "200" ]] || fail "Grafana /api/health returned $grafana_status through the ingress, expected 200"
echo "    OK: Grafana /api/health -> 200"

echo "==> monitoring-health: OK"
