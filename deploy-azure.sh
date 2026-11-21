#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-reposcope-rg}"
LOCATION="${AZURE_LOCATION:-eastus}"
APP_NAME="${AZURE_APP_NAME:-reposcope}"
ENVIRONMENT_NAME="${AZURE_ENVIRONMENT_NAME:-reposcope-env}"

if [[ "${AZURE_BUILD_LOCAL:-0}" == "1" ]] && ! docker info >/dev/null 2>&1; then
  echo "Start Docker Desktop or another Docker engine before deploying with AZURE_BUILD_LOCAL=1." >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Run 'az login' first." >&2
  exit 1
fi

SUBSCRIPTION_COUNT="$(az account list --refresh --query "length([?state=='Enabled'])" -o tsv)"
if [[ "$SUBSCRIPTION_COUNT" == "0" ]]; then
  echo "The signed-in Azure account has no active subscription." >&2
  exit 1
fi

if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  az account set --subscription "$AZURE_SUBSCRIPTION_ID"
fi

SUFFIX="$(openssl rand -hex 3)"
ACR_NAME="${AZURE_ACR_NAME:-reposcope${SUFFIX}}"

az extension add --name containerapp --upgrade >/dev/null
az provider register --namespace Microsoft.App --wait >/dev/null
az provider register --namespace Microsoft.ContainerRegistry --wait >/dev/null
az provider register --namespace Microsoft.OperationalInsights --wait >/dev/null

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true \
  --output none

REGISTRY_SERVER="$(az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --query loginServer -o tsv)"
IMAGE_NAME="${REGISTRY_SERVER}/reposcope:latest"

if [[ "${AZURE_BUILD_LOCAL:-0}" == "1" ]]; then
  docker build --platform linux/amd64 --tag "$IMAGE_NAME" .
  az acr login --name "$ACR_NAME"
  docker push "$IMAGE_NAME"
else
  az acr build \
    --registry "$ACR_NAME" \
    --image "reposcope:latest" \
    --file Dockerfile \
    .
fi

REGISTRY_USERNAME="$(az acr credential show --name "$ACR_NAME" --query username -o tsv)"
REGISTRY_PASSWORD="$(az acr credential show --name "$ACR_NAME" --query passwords[0].value -o tsv)"

DEPLOY_OUTPUT="$(
  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file azure-container-app.bicep \
    --parameters \
      location="$LOCATION" \
      appName="$APP_NAME" \
      environmentName="$ENVIRONMENT_NAME" \
      registryServer="$REGISTRY_SERVER" \
      registryUsername="$REGISTRY_USERNAME" \
      registryPassword="$REGISTRY_PASSWORD" \
      imageName="$IMAGE_NAME" \
    --query properties.outputs.url.value \
    -o tsv
)"

echo "$DEPLOY_OUTPUT"
