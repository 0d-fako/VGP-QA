// ─────────────────────────────────────────────────────────────────────────────
// azure-deploy.bicep
// Deploys the QA Test Agent to Azure App Service (Linux container) and wires
// it to your existing Azure PostgreSQL Flexible Server.
//
// Prerequisites:
//   1. az login && az account set -s <subscription>
//   2. An existing Azure Container Registry (ACR) with the image pushed:
//        docker build -t <acrName>.azurecr.io/qa-test-agent:latest .
//        az acr login -n <acrName>
//        docker push <acrName>.azurecr.io/qa-test-agent:latest
//   3. Your Azure PostgreSQL connection string ready as a parameter.
//
// Deploy:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file azure-deploy.bicep \
//     --parameters \
//         acrName=<acrName> \
//         claudeApiKey=<key> \
//         databaseUrl=<postgres://...> \
//         azureStorageConnectionString=<optional>
// ─────────────────────────────────────────────────────────────────────────────

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Short unique suffix appended to resource names (3-6 chars)')
param suffix string = uniqueString(resourceGroup().id)

// ── Parameters ───────────────────────────────────────────────────────────────

@description('Name of your Azure Container Registry (without .azurecr.io)')
param acrName string

@description('Tag of the container image to deploy')
param imageTag string = 'latest'

@description('Anthropic Claude API key')
@secure()
param claudeApiKey string

@description('PostgreSQL connection string (postgres://user:pass@host:5432/db?sslmode=require)')
@secure()
param databaseUrl string

@description('Azure Blob Storage connection string (leave empty to use local storage)')
@secure()
param azureStorageConnectionString string = ''

@description('Azure Blob Storage container name')
param azureContainerName string = 'test-evidence'

@description('Playwright headless mode (should be true in container)')
param playwrightHeadless string = 'true'

@description('App Service Plan SKU')
@allowed(['B2', 'B3', 'P1v3', 'P2v3'])
param appServiceSku string = 'P1v3'

// ── App Service Plan ─────────────────────────────────────────────────────────

resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: 'asp-qa-agent-${suffix}'
  location: location
  kind: 'linux'
  sku: {
    name: appServiceSku
  }
  properties: {
    reserved: true  // Required for Linux
  }
}

// ── ACR reference (must already exist) ───────────────────────────────────────

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

// ── App Service ───────────────────────────────────────────────────────────────

resource webApp 'Microsoft.Web/sites@2023-01-01' = {
  name: 'app-qa-agent-${suffix}'
  location: location
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${acr.properties.loginServer}/qa-test-agent:${imageTag}'
      acrUseManagedIdentityCreds: true
      alwaysOn: true
      minTlsVersion: '1.2'
      http20Enabled: true
      appSettings: [
        // ── App config ────────────────────────────────────────────────────
        { name: 'WEBSITES_PORT',                   value: '8501' }
        { name: 'DOCKER_REGISTRY_SERVER_URL',       value: 'https://${acr.properties.loginServer}' }
        // ── Secrets ───────────────────────────────────────────────────────
        { name: 'CLAUDE_API_KEY',                  value: claudeApiKey }
        { name: 'DATABASE_URL',                    value: databaseUrl }
        { name: 'AZURE_STORAGE_CONNECTION_STRING', value: azureStorageConnectionString }
        { name: 'AZURE_CONTAINER_NAME',            value: azureContainerName }
        // ── Playwright ────────────────────────────────────────────────────
        { name: 'PLAYWRIGHT_HEADLESS',             value: playwrightHeadless }
        { name: 'PLAYWRIGHT_TIMEOUT',              value: '30000' }
        { name: 'MAX_RETRIES',                     value: '3' }
        // ── Logging ───────────────────────────────────────────────────────
        { name: 'LOG_LEVEL',                       value: 'INFO' }
        { name: 'SCREENSHOTS_DIR',                 value: '/tmp/screenshots' }
      ]
    }
  }
}

// ── Grant App Service Managed Identity pull access to ACR ────────────────────
// Role: AcrPull (7f951dda-4ed3-4680-a7ca-43fe172d538d)

resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, webApp.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

@description('Public URL of the deployed QA Test Agent')
output appUrl string = 'https://${webApp.properties.defaultHostName}'

@description('App Service name (for CI/CD slot swaps)')
output appServiceName string = webApp.name

@description('Resource group')
output resourceGroup string = resourceGroup().name
