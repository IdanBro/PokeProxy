load('ext://helm_resource', 'helm_resource')
load('ext://uibutton', 'cmd_button', 'location', 'text_input')

allow_k8s_contexts('k3d-pokeproxy')

# >= helm's own --timeout=3m (180s) + the post-install E2E hook's
# activeDeadlineSeconds: 180, both inside the one process Tilt is timing.
# 240s already caused a real pending-upgrade incident once (step 1, see
# WORKLOG) with only ~1x margin; 480s gives real headroom.
update_settings(k8s_upsert_timeout_secs=480)

NAMESPACE = 'pokeproxy'
RELEASE = 'pokeproxy'
GIT_SHA = str(local('git rev-parse --short HEAD')).strip()

# No default_registry() call: Tilt auto-detects the k3d registry itself and
# logs that it ignores an explicit setting here -- confirmed live, the
# k3d-prefixed hostname a prior version of this line used
# ('k3d-pokeproxy-registry:5000') was also wrong; the real one is
# 'pokeproxy-registry:5000', no 'k3d-' prefix.

k8s_yaml('deploy/k8s/namespace.yaml')
k8s_resource(new_name='namespace', objects=['pokeproxy:namespace'])

local_resource(
    'sealing-key',
    cmd=['bash', 'scripts/seal-hmac.sh', '--env', 'local'],
    labels=['app'],
)

local_resource(
    'monitoring',
    cmd=['bash', 'scripts/install-monitoring.sh'],
    env={'KUBE_CONTEXT': 'k3d-pokeproxy'},
    resource_deps=['namespace'],
    labels=['app'],
)

docker_build(
    'pokeproxy',
    'app',
    dockerfile='app/Dockerfile',
    build_args={'GIT_SHA': GIT_SHA},
    only=['pyproject.toml', 'uv.lock', 'src'],
)

docker_build(
    'mock-downstream',
    'app',
    dockerfile='app/Dockerfile.mock',
    build_args={'GIT_SHA': GIT_SHA},
    only=['mock_service'],
)

docker_build(
    'pokeproxy-e2e',
    'app',
    dockerfile='app/Dockerfile.e2e',
    build_args={'BASE_IMAGE': 'pokeproxy'},
    only=['e2e'],
)

# NOTE: k8s_resource(objects=[...], new_name=...) does NOT work here.
# Verified live: it errored at Tiltfile-load time with "Possible objects
# are:" (empty) because k8s_custom_deploy (what ext://helm_resource wraps)
# only knows its objects *after* apply_cmd runs at build time; the object
# list is not available during Tiltfile static evaluation the way it is for
# k8s_yaml(). Real per-workload Tilt resources come from the local_resource
# shims below instead: status-only, read-only against the cluster, never a
# second applier of the chart's objects.
helm_resource(
    'pokeproxy-helm',
    'deploy/helm/pokeproxy',
    release_name=RELEASE,
    namespace=NAMESPACE,
    flags=[
        '-f', 'deploy/envs/local/values.yaml',
        '--set', 'e2e.enabled=true',
        '--atomic', '--timeout=3m',
    ],
    image_deps=['pokeproxy', 'mock-downstream', 'pokeproxy-e2e'],
    image_keys=[
        ('components.pokeproxy.image.repository', 'components.pokeproxy.image.tag'),
        ('components.mock-downstream.image.repository', 'components.mock-downstream.image.tag'),
        ('e2e.image.repository', 'e2e.image.tag'),
    ],
    resource_deps=['sealing-key', 'namespace', 'monitoring'],
)

# `helm upgrade --wait` above only proves pods came Ready -- it asserts
# nothing about Prometheus actually scraping pokeproxy or the alert rules
# loading without errors, the exact class of bug that bit Part 4 twice
# (a permanently-down Grafana scrape target, two silently-empty dashboard
# panels). Depends on `pokeproxy-helm` directly so `tilt ci` gates on it.
local_resource(
    'monitoring-health',
    cmd=['bash', 'scripts/monitoring-health.sh'],
    env={'KUBE_CONTEXT': 'k3d-pokeproxy'},
    resource_deps=['pokeproxy-helm'],
    labels=['app'],
)

def workload_status_resource(ui_name, deployment, component_label):
    local_resource(
        ui_name,
        cmd=['kubectl', 'rollout', 'status', 'deployment/' + deployment, '-n', NAMESPACE, '--timeout=180s'],
        serve_cmd=['kubectl', 'logs', '-f', '-n', NAMESPACE,
                    '-l', 'app.kubernetes.io/name=%s,app.kubernetes.io/instance=%s' % (component_label, RELEASE),
                    '--tail=50', '--all-containers=true', '--ignore-errors'],
        resource_deps=['pokeproxy-helm'],
        labels=['app'],
    )

workload_status_resource('pokeproxy', 'pokeproxy', 'pokeproxy')
workload_status_resource('redis', 'pokeproxy-redis', 'redis')
workload_status_resource('mock-downstream', 'pokeproxy-mock-downstream', 'mock-downstream')

local_resource(
    'e2e',
    cmd=['bash', 'scripts/run-e2e-now.sh'],
    resource_deps=['pokeproxy-helm'],
    auto_init=False,
    labels=['debug'],
)

# Auto (not auto_init=False), unlike `e2e` above: this is deploy.sh's old
# step-7 external-reachability probe (`curl localhost:8080/stream`, host
# network through k3d's port mapping), which nothing else in this Tiltfile
# replaces automatically -- the real E2E gate lives inside the Helm
# post-install hook via --atomic and only exercises the *in-cluster* Traefik
# path. Depends on `pokeproxy-helm` directly, not `e2e`: an auto resource
# depending on an auto_init=False one sits blocked forever waiting for a
# trigger that never comes on its own (confirmed live in step 5 -- `tilt
# get uiresources ingress-probe` showed `waiting-for-dep` on `e2e` until
# manually triggered), so `tilt ci` would hang without this.
local_resource(
    'ingress-probe',
    cmd=['bash', '-c',
         'code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/stream); echo "unsigned POST /stream -> $code"; [ "$code" = "401" ]'],
    resource_deps=['pokeproxy-helm'],
    labels=['app'],
)

cmd_button(
    'send-signed-traffic',
    resource='pokeproxy',
    argv=['bash', '-c',
          'cd app && .venv/bin/python3 scripts/load_generator.py --url http://localhost:8080/stream --rps "$RPS" --duration "$DURATION"'],
    location=location.RESOURCE,
    icon_name='bolt',
    text='Send signed traffic',
    inputs=[
        text_input('RPS', label='Requests/sec', default='10'),
        text_input('DURATION', label='Duration (s)', default='10'),
    ],
)

cmd_button(
    'run-e2e-now',
    resource='e2e',
    argv=['tilt', 'trigger', 'e2e'],
    location=location.RESOURCE,
    icon_name='play_arrow',
    text='Run E2E now',
)

cmd_button(
    'flush-redis-cache',
    resource='redis',
    argv=['kubectl', 'exec', '-n', NAMESPACE, 'deploy/pokeproxy-redis', '--', 'redis-cli', 'FLUSHALL'],
    location=location.RESOURCE,
    icon_name='delete_sweep',
    text='Flush Redis cache',
)

cmd_button(
    'break-rules',
    resource='pokeproxy',
    argv=['bash', 'scripts/break-rules.sh'],
    location=location.RESOURCE,
    icon_name='report',
    text='Break rules.json (scenario B)',
    requires_confirmation=True,
)

cmd_button(
    'restore-rules',
    resource='pokeproxy',
    argv=['bash', 'scripts/restore-rules.sh'],
    location=location.RESOURCE,
    icon_name='restore',
    text='Restore rules.json',
)
