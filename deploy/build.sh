#!/usr/bin/env bash
# Build ArchIDE images from the single source tree.
#
#   ./deploy/build.sh                      # local image (docker run on a workstation)
#   ./deploy/build.sh --tag 1.0.0          # explicit tag
#   ./deploy/build.sh --push               # also push to $REGISTRY (Kubernetes)
#   REGISTRY=192.168.20.254:30003/dev ./deploy/build.sh --push
#
# The same image serves both deployments: the local all-in-one container and the
# Kubernetes deployment in deploy/helm/archide.
set -euo pipefail

cd "$(dirname "$0")/.."

NAME="${NAME:-archide}"
TAG="latest"
REGISTRY="${REGISTRY:-}"
PUSH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?}"; shift 2 ;;
    --push) PUSH=1; shift ;;
    --registry) REGISTRY="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

LOCAL="${NAME}:${TAG}"
docker build -f deploy/Dockerfile -t "$LOCAL" .
echo "built $LOCAL"

if [ "$PUSH" = "1" ]; then
  if [ -z "$REGISTRY" ]; then
    echo "error: --push needs REGISTRY (e.g. REGISTRY=192.168.20.254:30003/dev)" >&2
    exit 2
  fi
  REMOTE="${REGISTRY%/}/${NAME}:${TAG}"
  docker tag "$LOCAL" "$REMOTE"
  docker push "$REMOTE"
  DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "$REMOTE" 2>/dev/null || true)"
  echo "pushed $REMOTE"
  [ -n "$DIGEST" ] && echo "digest: $DIGEST"
  echo
  echo "deploy with:"
  echo "  helm upgrade --install archide deploy/helm/archide -n archide --create-namespace \\"
  echo "    -f deploy/helm/archide/values.yaml -f deploy/helm/archide/values-farm.yaml \\"
  echo "    --set image.repository=${REMOTE%:*} --set image.tag=${TAG}"
fi
