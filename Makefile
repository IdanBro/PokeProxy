.DEFAULT_GOAL := help

.PHONY: up dev down up-prod down-prod status install-tools help

install-tools: ## Check for and install missing CLI tools from official sources (asks before every step)
	bash scripts/install-tools.sh

up: ## Zero to running, non-interactive: preflight, cluster, tilt ci
	bash scripts/up.sh ci

dev: ## Zero to running, interactive dev loop: preflight, cluster, tilt up
	bash scripts/up.sh dev

down: ## Tear down the local cluster (tilt down, then k3d cluster delete)
	bash scripts/down.sh

up-prod: ## Bootstrap the prod stand-in (k3d + Argo CD, unchanged from Part 3)
	bash scripts/bootstrap-prod.sh

down-prod: ## Delete the prod stand-in cluster
	bash scripts/down-prod.sh

status: ## Show cluster, pods, Helm releases, and an ingress probe
	bash scripts/status.sh

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "%-12s %s\n", $$1, $$2}'
