load('ext://helm_resource', 'helm_resource')
load('ext://uibutton', 'cmd_button', 'location', 'text_input')

allow_k8s_contexts('k3d-pokeproxy')

# Must exceed helm's own --timeout=4m, which runs inside the single process
# Tilt is timing. 240s left almost no margin and produced a stuck
# pending-upgrade.
update_settings(k8s_upsert_timeout_secs=480)

NAMESPACE = 'pokeproxy'
RELEASE = 'pokeproxy'
GIT_SHA = str(local('git rev-parse --short HEAD')).strip()

# No default_registry(): Tilt auto-detects the k3d registry and logs that it
# ignores an explicit setting here.

k8s_yaml('deploy/k8s/namespace.yaml')
k8s_resource(new_name='namespace', objects=['pokeproxy:namespace'])

local_resource(
    'monitoring',
    cmd=['bash', 'scripts/install-monitoring.sh'],
    env={'KUBE_CONTEXT': 'k3d-pokeproxy'},
    resource_deps=['namespace'],
    labels=['app'],
    links=[
        link('http://localhost:8080/grafana/', 'Grafana'),
    ],
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

# ext://helm_resource runs a real `helm upgrade --install`, so --atomic and the
# post-install E2E hook stay a deployment gate. Tilt's built-in helm() is
# `helm template`: no release, no hooks, no rollback.
#
# k8s_resource(objects=[...]) cannot split this into per-workload resources --
# the object list only exists after apply_cmd runs, not during Tiltfile
# evaluation. The read-only local_resource shims below fill that gap instead.
helm_resource(
    'pokeproxy-helm',
    'deploy/helm/pokeproxy',
    release_name=RELEASE,
    namespace=NAMESPACE,
    flags=[
        '-f', 'deploy/envs/local/values.yaml',
        '--set', 'e2e.enabled=true',
        '--atomic', '--timeout=4m',
    ],
    image_deps=['pokeproxy', 'mock-downstream', 'pokeproxy-e2e'],
    image_keys=[
        ('components.pokeproxy.image.repository', 'components.pokeproxy.image.tag'),
        ('components.mock-downstream.image.repository', 'components.mock-downstream.image.tag'),
        ('e2e.image.repository', 'e2e.image.tag'),
    ],
    resource_deps=['namespace', 'monitoring'],
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

# The Helm post-install E2E hook only exercises the in-cluster path. This is the
# external one: host network, through k3d's port mapping into Traefik.
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
          'cd app && uv run python scripts/load_generator.py --url http://localhost:8080/stream --rps "$RPS" --duration "$DURATION"'],
    location=location.RESOURCE,
    icon_name='bolt',
    text='Send signed traffic',
    inputs=[
        text_input('RPS', label='Requests/sec', default='10'),
        text_input('DURATION', label='Duration (s)', default='10'),
    ],
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
    text='Break rules.json (rollback demo)',
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
