#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="pokeproxy"
RELEASE="pokeproxy"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-pokeproxy}"
JOB_NAME="pokeproxy-e2e-manual-$(date +%s)"

values_json="$(helm get values "$RELEASE" -n "$NAMESPACE" --kube-context "$KUBE_CONTEXT" -a -o json)"
e2e_repo="$(echo "$values_json" | jq -r '.e2e.image.repository')"
e2e_tag="$(echo "$values_json" | jq -r '.e2e.image.tag')"
proxy_url="$(echo "$values_json" | jq -r '.e2e.proxyUrl')"
timeout_seconds="$(echo "$values_json" | jq -r '.e2e.timeoutSeconds')"
retries="$(echo "$values_json" | jq -r '.e2e.retries')"
mock_port="$(echo "$values_json" | jq -r '.components["mock-downstream"].port')"
mock_url="http://${RELEASE}-mock-downstream:${mock_port}"

echo "Running pokeproxy-e2e as a one-off Job: image ${e2e_repo}:${e2e_tag}"

cat <<EOF | kubectl apply --context "$KUBE_CONTEXT" -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: e2e
    app.kubernetes.io/instance: ${RELEASE}
    app.kubernetes.io/part-of: pokeproxy
    app.kubernetes.io/component: e2e-manual
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 180
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        app.kubernetes.io/name: e2e
        app.kubernetes.io/instance: ${RELEASE}
        app.kubernetes.io/part-of: pokeproxy
        app.kubernetes.io/component: e2e-manual
    spec:
      automountServiceAccountToken: false
      enableServiceLinks: false
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: e2e
          image: "${e2e_repo}:${e2e_tag}"
          args:
            - --proxy-url=${proxy_url}
            - --mock-url=${mock_url}
            - --timeout=${timeout_seconds}
            - --retries=${retries}
          envFrom:
            - secretRef:
                name: pokeproxy-hmac
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
EOF

deadline=$(( $(date +%s) + 90 ))
status="timeout"
while (( $(date +%s) < deadline )); do
  succeeded="$(kubectl get job "$JOB_NAME" -n "$NAMESPACE" --context "$KUBE_CONTEXT" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
  failed="$(kubectl get job "$JOB_NAME" -n "$NAMESPACE" --context "$KUBE_CONTEXT" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
  if [[ "$succeeded" == "1" ]]; then
    status="pass"
    break
  fi
  if [[ -n "$failed" && "$failed" -ge 1 ]]; then
    status="fail"
    break
  fi
  sleep 2
done

echo "== e2e result: $status =="
kubectl logs "job/$JOB_NAME" -n "$NAMESPACE" --context "$KUBE_CONTEXT" --all-containers=true 2>&1 || true
kubectl delete job "$JOB_NAME" -n "$NAMESPACE" --context "$KUBE_CONTEXT" --ignore-not-found >/dev/null

if [[ "$status" != "pass" ]]; then
  exit 1
fi
